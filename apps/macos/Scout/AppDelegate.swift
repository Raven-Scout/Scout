import AppKit
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    /// Opens a run by id; `ScoutApp` points it at `AppState.openRun(id:)`.
    /// Static because the delegate adaptor builds this object itself.
    @MainActor static var openRun: ((Run.ID) -> Void)?
    /// Fires a schedule from a notification's "Run again"; `ScoutApp` points
    /// it at `AppState.retryFromNotification(slotKey:)`.
    @MainActor static var retrySlot: ((String) -> Void)?
    /// Brings up Sparkle's alert for the update a clicked "Scout X is ready"
    /// notification announced; `ScoutApp` points it at `UpdateService`.
    @MainActor static var showAppUpdate: (() -> Void)?

    func applicationDidFinishLaunching(_: Notification) {
        UNUserNotificationCenter.current().delegate = self
        NotificationService.registerCategories()
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

    /// Unsaved KB drafts (`KBDraftStore`) and slot-form drafts
    /// (`SlotDraftStore`) live in memory, so quitting would lose them. Ask
    /// first.
    func applicationShouldTerminate(_: NSApplication) -> NSApplication.TerminateReply {
        let notes = KBDraftStore.shared.unsavedFileNames
        let slots = SlotDraftStore.shared.unsavedSlotNames
        guard !notes.isEmpty || !slots.isEmpty else { return .terminateNow }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "You have unsaved changes"
        alert.informativeText = Self.unsavedDraftsMessage(notes: notes, slots: slots)
        alert.addButton(withTitle: "Keep Editing")   // first = default, Return
        alert.addButton(withTitle: "Quit Anyway")
        return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
    }

    static func unsavedDraftsMessage(_ names: [String]) -> String {
        unsavedDraftsMessage(notes: names, slots: [])
    }

    static func unsavedDraftsMessage(notes: [String], slots: [String]) -> String {
        var parts: [String] = []
        if !notes.isEmpty {
            parts.append(listed(notes, one: "knowledge-base note has", many: "knowledge-base notes have"))
        }
        if !slots.isEmpty {
            parts.append(listed(slots, one: "schedule has", many: "schedules have"))
        }
        return parts.joined(separator: ", and ") + ". Quitting discards them."
    }

    private static func listed(_ names: [String], one: String, many: String) -> String {
        let shown = names.prefix(5).joined(separator: ", ")
        let more = names.count > 5 ? " and \(names.count - 5) more" : ""
        return "\(names.count) \(names.count == 1 ? one : many) unsaved edits (\(shown)\(more))"
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler(NotificationService.presentationOptions(for: notification.request.content.userInfo))
    }

    /// Clicking a run notification opens that run in the Control Center;
    /// its "Run again" fires the run's own schedule. Clicking the update
    /// notification brings up Sparkle's alert for that update.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        switch Self.click(actionIdentifier: response.actionIdentifier,
                          userInfo: response.notification.request.content.userInfo) {
        case .showAppUpdate: Self.showAppUpdate?()
        case .openRun(let id): Self.openRun?(id)
        case .retrySlot(let slot): Self.retrySlot?(slot)
        case .nothing: break
        }
        completionHandler()
    }

    /// What a click on one of Scout's notifications does. The update
    /// notification is checked first: it is the one that carries
    /// `AppUpdateNotifier.versionKey`.
    enum NotificationClick: Equatable {
        case showAppUpdate
        case openRun(Run.ID)
        case retrySlot(String)
        case nothing
    }

    nonisolated static func click(actionIdentifier: String,
                                  userInfo: [AnyHashable: Any]) -> NotificationClick {
        switch actionIdentifier {
        case UNNotificationDefaultActionIdentifier:
            if AppUpdateNotifier.isAppUpdate(userInfo) { return .showAppUpdate }
            if let id = NotificationService.runID(from: userInfo) { return .openRun(id) }
            return .nothing
        case NotificationService.retryActionID:
            return NotificationService.slotKey(from: userInfo).map { .retrySlot($0) } ?? .nothing
        default:
            return .nothing
        }
    }
}
