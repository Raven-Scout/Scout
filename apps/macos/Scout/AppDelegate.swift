import AppKit
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    /// Opens a run by id; `ScoutApp` points it at `AppState.openRun(id:)`.
    /// Static because the delegate adaptor builds this object itself.
    @MainActor static var openRun: ((Run.ID) -> Void)?

    func applicationDidFinishLaunching(_: Notification) {
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(
            options: [.alert, .sound, .badge]
        ) { _, _ in }
        // "Start in menu bar" is handled by the main Window scene's
        // .defaultLaunchBehavior(.suppressed) in ScoutApp — no window is
        // created, so there is nothing to hide here.
    }

    func applicationShouldTerminateAfterLastWindowClosed(_: NSApplication) -> Bool {
        // Keep the app alive so the menu-bar extra stays present after the
        // main window is closed.
        false
    }

    /// Unsaved KB drafts live in memory (`KBDraftStore`), so quitting would
    /// lose them. Ask first.
    func applicationShouldTerminate(_: NSApplication) -> NSApplication.TerminateReply {
        let names = KBDraftStore.shared.unsavedFileNames
        guard !names.isEmpty else { return .terminateNow }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "You have unsaved changes"
        alert.informativeText = Self.unsavedDraftsMessage(names)
        alert.addButton(withTitle: "Keep Editing")   // first = default, Return
        alert.addButton(withTitle: "Quit Anyway")
        return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
    }

    static func unsavedDraftsMessage(_ names: [String]) -> String {
        let shown = names.prefix(5).joined(separator: ", ")
        let more = names.count > 5 ? " and \(names.count - 5) more" : ""
        let noun = names.count == 1 ? "note has" : "notes have"
        return "\(names.count) knowledge-base \(noun) unsaved edits (\(shown)\(more)). Quitting discards them."
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }

    /// Clicking a run notification opens that run in the Control Center.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier,
           let id = NotificationService.runID(from: response.notification.request.content.userInfo) {
            Self.openRun?(id)
        }
        completionHandler()
    }
}
