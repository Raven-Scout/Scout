import Combine
import SwiftUI

/// Redraws `content`, and only `content`, when one of `objects` changes.
///
/// `AppState` does not re-publish its services' changes: that redrew every
/// view holding `AppState`, and `ScoutApp.body` with every scene, on any
/// service's change (a usage refresh redrew the sidebar and Settings). A
/// view that reads a service through `state` wraps what reads it in this,
/// naming the services it reads, so it follows those services and no others:
///
///     Observing(state.scoutUsageService, state.sessionLogService) { card }
///
/// The objects are taken once, when the view first appears: pass objects
/// that live as long as the view does (`AppState`'s services are `let`s).
struct Observing<Content: View>: View {
    @StateObject private var relay: ChangeRelay
    private let content: () -> Content

    init(_ objects: any ObservableObject..., @ViewBuilder content: @escaping () -> Content) {
        _relay = StateObject(wrappedValue: ChangeRelay(objects))
        self.content = content
    }

    var body: some View { content() }
}

/// One `objectWillChange` for several objects: fires, synchronously, whenever
/// any of them is about to change, as `@ObservedObject` would for each.
final class ChangeRelay: ObservableObject {
    private var subscriptions: [AnyCancellable] = []

    init(_ objects: [any ObservableObject]) {
        for object in objects { relay(object) }
    }

    private func relay<Object: ObservableObject>(_ object: Object) {
        object.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &subscriptions)
    }
}
