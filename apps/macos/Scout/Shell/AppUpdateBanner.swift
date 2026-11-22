import SwiftUI

/// "Scout X is ready" across the top of every main-window page, while
/// `UpdateService.showsAppUpdateBanner` holds. Update now brings up Sparkle's
/// alert for it, where the user installs it; while Sparkle is still busy
/// with it (a background download), the click waits and the banner says so.
/// The × closes it for this version until Scout relaunches.
struct AppUpdateBanner: View {
    @ObservedObject var updates: UpdateService

    var body: some View {
        if updates.showsAppUpdateBanner, let version = updates.appUpdate.latestVersion {
            let pending = updates.appUpdateShowPending
            DSBanner(tone: .info,
                     title: Self.title(version: version),
                     message: pending ? Self.pendingMessage
                                      : Self.message(current: updates.appUpdate.currentVersion),
                     symbol: "arrow.down.circle.fill",
                     actions: pending ? [] : [ProblemAction("Update now") { updates.showAppUpdate() }],
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
        let action = "Click Update now to see what's new and install it."
        guard let current else { return action }
        return "You have \(current). \(action)"
    }

    /// While a click on Update now waits for Sparkle.
    nonisolated static let pendingMessage = "Getting the update ready. It opens as soon as it's done."
}
