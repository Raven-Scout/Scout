import SwiftUI

/// "Check for Updates…" menu item, shared by the app menu (under About Scout)
/// and the menu-bar extra. Disabled while a check is in flight, while
/// Sparkle's `canCheckForUpdates` is false (a background check or permission
/// prompt is running), and in Debug builds, where the updater never starts.
struct CheckForUpdatesView: View {
    @ObservedObject var updates: UpdateService

    var body: some View {
        Button("Check for Updates…") { updates.check(.app) }
            .disabled(!updates.appCheckAvailable)
    }
}
