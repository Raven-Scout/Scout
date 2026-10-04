import SwiftUI
import Testing
@testable import Scout

@MainActor
@Suite("Display profile renders", .serialized)
struct DisplayProfileRenderTests {
    struct Case: CustomTestStringConvertible, Sendable {
        let view: ActionItemsViewMode
        let density: ActionItemsDisplay.Density
        let sort: ActionItemsDisplay.Sort
        let grouping: ActionItemsDisplay.Grouping
        let fields: ActionItemsDisplay.Fields
        let dark: Bool

        var display: ActionItemsDisplay {
            ActionItemsDisplay(defaultView: view, sort: sort, grouping: grouping, density: density, fields: fields)
        }
        var values: [String] {
            [view.rawValue, density.rawValue, sort.rawValue, grouping.rawValue,
             "\(fields.refs)", "\(fields.snooze)", "\(fields.comments)", "\(dark)"]
        }
        var testDescription: String { values.joined(separator: " ") }
    }

    private static func f(_ refs: Bool, _ snooze: Bool, _ comments: Bool) -> ActionItemsDisplay.Fields {
        .init(refs: refs, snooze: snooze, comments: comments)
    }

    /// Generated as a strength-2 covering array; `coversEveryPair` checks it.
    static let cases: [Case] = [
        Case(view: .list,  density: .compact,     sort: .fileOrder,    grouping: .section, fields: f(true, true, true),    dark: false),
        Case(view: .board, density: .compact,     sort: .alphabetical, grouping: .none,    fields: f(false, false, false), dark: true),
        Case(view: .list,  density: .comfortable, sort: .fileOrder,    grouping: .section, fields: f(true, false, false),  dark: true),
        Case(view: .board, density: .comfortable, sort: .alphabetical, grouping: .section, fields: f(false, true, true),   dark: false),
        Case(view: .list,  density: .comfortable, sort: .fileOrder,    grouping: .none,    fields: f(false, true, false),  dark: false),
        Case(view: .list,  density: .comfortable, sort: .alphabetical, grouping: .none,    fields: f(true, true, true),    dark: true),
        Case(view: .board, density: .compact,     sort: .fileOrder,    grouping: .section, fields: f(true, false, true),   dark: false),
    ]

    @Test func coversEveryPair() {
        let domains: [Set<String>] = [
            ["list", "board"], ["comfortable", "compact"], ["fileOrder", "alphabetical"], ["section", "none"],
            ["true", "false"], ["true", "false"], ["true", "false"], ["true", "false"],
        ]
        for i in domains.indices {
            for j in domains.indices where j > i {
                for a in domains[i] {
                    for b in domains[j] {
                        #expect(Self.cases.contains { $0.values[i] == a && $0.values[j] == b }, "missing pair \(i)=\(a), \(j)=\(b)")
                    }
                }
            }
        }
    }

    @Test(arguments: cases)
    func renders(_ c: Case) async throws {
        let vault = try SmokeVault()
        defer { vault.tearDown() }
        vault.state.displayProfileService.update { $0 = c.display }
        await vault.loadDocuments()
        ViewHost.render(
            ActionItemsView(
                scoutDirectory: vault.state.scoutDirectory,
                actionItemsDirectory: vault.state.actionItemsDirectory)
                .environmentObject(vault.state.actionItemsDocumentService)
                .environmentObject(vault.state.actionItemsWriterBox)
                .environmentObject(vault.state.actionItemsEnvState)
                .environmentObject(vault.state.displayProfileService)
                .environment(\.colorScheme, c.dark ? .dark : .light))
    }
}
