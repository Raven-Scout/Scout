import SwiftUI
import AppKit

/// Reduce Motion (System Settings ▸ Accessibility ▸ Display), honoured in one
/// place. Use these instead of bare `withAnimation` / `.animation(_:value:)`:
///
/// - `withMotion(.easeInOut) { … }` — applies the change without animating
///   when Reduce Motion is on.
/// - `.motionAnimation(.easeInOut, value: x)` — the same for implicit
///   animations, read from the view's environment.
/// - `.motionTransition(.move(edge: .top))` — slides become a cross-fade.
enum Motion {
    /// Pure rule: the animation to run, or `nil` (apply instantly) when the
    /// user asked for reduced motion.
    nonisolated static func animation(_ animation: Animation?, reduceMotion: Bool) -> Animation? {
        reduceMotion ? nil : animation
    }

    /// Pure rule: a moving transition becomes a cross-fade under Reduce Motion.
    nonisolated static func transition(_ transition: AnyTransition, reduceMotion: Bool) -> AnyTransition {
        reduceMotion ? .opacity : transition
    }

    /// The system setting, for code outside a view's environment.
    static var reduceMotion: Bool {
        NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    }
}

/// `withAnimation` that skips the animation under Reduce Motion.
@discardableResult
func withMotion<Result>(_ animation: Animation? = .default, _ body: () throws -> Result) rethrows -> Result {
    if Motion.reduceMotion { return try body() }
    return try withAnimation(animation, body)
}

private struct MotionAnimation<Value: Equatable>: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let animation: Animation?
    let value: Value

    func body(content: Content) -> some View {
        content.animation(Motion.animation(animation, reduceMotion: reduceMotion), value: value)
    }
}

private struct MotionTransition: ViewModifier {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let transition: AnyTransition

    func body(content: Content) -> some View {
        content.transition(Motion.transition(transition, reduceMotion: reduceMotion))
    }
}

extension View {
    /// `.animation(_:value:)` that doesn't animate under Reduce Motion.
    func motionAnimation<Value: Equatable>(_ animation: Animation?, value: Value) -> some View {
        modifier(MotionAnimation(animation: animation, value: value))
    }

    /// `.transition(_:)` that cross-fades instead of moving under Reduce Motion.
    func motionTransition(_ transition: AnyTransition) -> some View {
        modifier(MotionTransition(transition: transition))
    }
}
