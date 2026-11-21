import SwiftUI

/// "Scout X is ready" across the top of every main-window page, while
/// `UpdateService.showsAppUpdateBanner` holds. Update Now brings up Sparkle's
/// alert for it (Install and Relaunch, or the download first); the × closes
/// it for this version until Scout relaunches.
struct AppUpdateBanner: View {
    @ObservedObject var updates: UpdateService

    var body: some View {
        if updates.showsAppUpdateBanner, let version = updates.appUpdate.latestVersion {
            DSBanner(tone: .info,
                     title: Self.title(version: version),
                     message: Self.message(current: updates.appUpdate.currentVersion),
                     symbol: "arrow.down.circle.fill",
                     actions: [ProblemAction("Update Now") { updates.check(.app) }],
                     onDismiss: { updates.dismissAppUpdateBanner() })
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 4)
        }
    }

    nonisolated static func title(version: String) -> String {
        "Scout \(version) is ready"
    }

    nonisolated static func message(current: String?) -> String {
        let relaunch = "Updating relaunches Scout in a few seconds."
        guard let current else { return relaunch }
        return "You have \(current). \(relaunch)"
    }
}
