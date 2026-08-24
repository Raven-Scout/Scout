import AppKit
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_: Notification) {
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(
            options: [.alert, .sound, .badge]
        ) { _, _ in }

        guard UserDefaults.standard.bool(forKey: "launchMinimized") else { return }
        // SwiftUI creates its WindowGroup during launch. Defer one run-loop turn
        // so the window exists, then hide it without closing the scene; the
        // menu-bar panel can bring the same window back later.
        DispatchQueue.main.async {
            NSApp.windows.first(where: { $0.title == "Scout" })?.orderOut(nil)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_: NSApplication) -> Bool {
        // Keep the app alive so the menu-bar extra stays present after the
        // main window is closed.
        false
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}
