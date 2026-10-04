import Foundation
import SwiftUI
import Testing
@testable import Scout

/// #52: the board card gets the same Launch Claude menu as the list card,
/// under the same rule: open tasks only.
@MainActor
@Suite("Board card Launch Claude menu")
struct BoardCardLaunchMenuTests {
    static let cases: [(done: Bool, shows: Bool)] = [(false, true), (true, false)]

    @Test(arguments: cases)
    func launchMenuOnlyOnOpenTasks(_ c: (done: Bool, shows: Bool)) {
        #expect(BoardCardView.showsLaunchMenu(for: SmokeFixtures.task(done: c.done)) == c.shows)
    }

    @Test("the launch menu renders in both styles")
    func launchMenuRendersBothStyles() {
        for style in [LaunchClaudeMenu.Style.split, .icon] {
            ViewHost.render(
                LaunchClaudeMenu(
                    task: SmokeFixtures.task(),
                    scoutDirectory: URL(fileURLWithPath: "/tmp/scout"),
                    style: style,
                    launchError: .constant(nil)),
                size: CGSize(width: 400, height: 60))
        }
    }
}
