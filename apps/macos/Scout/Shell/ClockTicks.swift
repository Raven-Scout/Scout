import Combine
import Foundation

/// Publishes when the calendar day changes: midnight, a wake past midnight,
/// or a time-zone or clock change. For views that show "today" or label days
/// relative to it (the activity heatmap, the connector card's columns), listed
/// in their `Observing` beside the services they read: those services publish
/// only when their data changes, so an app left open overnight would otherwise
/// keep showing yesterday as today until the next run.
final class DayTick: ObservableObject {
    static let shared = DayTick()

    private var subscriptions: [AnyCancellable] = []

    init(center: NotificationCenter = .default) {
        for name in [Notification.Name.NSCalendarDayChanged, .NSSystemTimeZoneDidChange, .NSSystemClockDidChange] {
            center.publisher(for: name)
                // Posted on whichever thread noticed; views read on the main one.
                .receive(on: DispatchQueue.main)
                .sink { [weak self] _ in self?.objectWillChange.send() }
                .store(in: &subscriptions)
        }
    }
}

/// Publishes once a minute. For views that print relative times ("started 5
/// minutes ago", "next in 20 min"), listed in their `Observing`: no service
/// they read is guaranteed to publish that often, and one that happens to
/// today (`ScheduleService` re-assigns `upcoming` every poll) may stop when it
/// learns to skip an unchanged value.
final class MinuteTick: ObservableObject {
    static let shared = MinuteTick()

    private var timer: AnyCancellable?

    init(every interval: TimeInterval = 60) {
        timer = Timer.publish(every: interval, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in self?.objectWillChange.send() }
    }
}
