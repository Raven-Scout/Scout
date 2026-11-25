import AppKit
import SwiftUI
@testable import Scout

/// Reads the accessibility tree SwiftUI builds for a view, as VoiceOver would
/// see it, so tests can assert labels, values, selected state and
/// identifiers.
///
/// SwiftUI only builds that tree when an assistive client asks for it, so a
/// plain `NSHostingView` reports one empty group. Setting the
/// `AXEnhancedUserInterface` attribute on the app — what assistive tools set
/// to switch an app's accessibility on (`AXManualAccessibility` is the
/// Chromium-style twin, set too) — makes it build the tree for hosting views.
@MainActor
enum AccessibilityTree {

    struct Node: CustomStringConvertible {
        let role: String
        let label: String
        let value: String
        let identifier: String
        let isSelected: Bool

        var description: String {
            "\(role) label=\"\(label)\" value=\"\(value)\" id=\"\(identifier)\" selected=\(isSelected)"
        }
    }

    private static let attributes = ["AXEnhancedUserInterface", "AXManualAccessibility"]
        .map { NSAccessibility.Attribute(rawValue: $0) }

    /// How many readers want the tree built. The switch is app-wide and suites
    /// run side by side, so one reader finishing must not turn it off under
    /// another (`LiveAccessibilityTree` keeps it on for a whole test).
    private static var readers = 0

    fileprivate static func switchOn() {
        readers += 1
        if readers == 1 { for attribute in attributes { NSApp.accessibilitySetValue(true, forAttribute: attribute) } }
    }

    fileprivate static func switchOff() {
        readers = max(0, readers - 1)
        if readers == 0 { for attribute in attributes { NSApp.accessibilitySetValue(false, forAttribute: attribute) } }
    }

    /// Every element under `view`, depth first.
    ///
    /// `async` because inside the running test-host app SwiftUI picks up the
    /// switch, and fills a new hosting view's tree, on a later main-run-loop
    /// turn. Awaiting a short sleep yields the main actor so that turn
    /// happens, without re-entering the run loop (which crashes the host).
    /// The switch is turned off again before returning, so other suites run
    /// with the host as they always did.
    static func nodes<V: View>(of view: V, size: CGSize = CGSize(width: 900, height: 700)) async -> [Node] {
        switchOn()
        defer { switchOff() }
        try? await Task.sleep(for: .milliseconds(300))
        let host = NSHostingView(rootView: view)
        host.frame = CGRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled, .resizable],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        host.display()
        var out: [Node] = []
        // Give the tree a few turns to fill in (a List's rows come after its
        // container); stop once two reads in a row agree.
        var previous = -1
        for _ in 0..<15 {
            out = []
            collect(host, into: &out, depth: 0)
            if out.count > 1 && out.count == previous { break }
            previous = out.count
            try? await Task.sleep(for: .milliseconds(100))
            host.layoutSubtreeIfNeeded()
            host.display()
        }
        window.contentView = nil
        return out
    }

    fileprivate static func collect(_ element: Any, into out: inout [Node], depth: Int) {
        guard depth < 40, let object = element as? NSObject else { return }
        // Modern elements answer the `accessibility…` methods; AppKit's table
        // and outline rows (a sidebar `List`) only answer the attribute API.
        func attribute(_ name: NSAccessibility.Attribute) -> Any? {
            guard object.responds(to: #selector(NSObject.accessibilityAttributeValue(_:))) else { return nil }
            return object.accessibilityAttributeValue(name)
        }
        func value(_ method: String, _ legacy: NSAccessibility.Attribute) -> Any? {
            if object.responds(to: NSSelectorFromString(method)), let v = object.value(forKey: method) {
                if let s = v as? String, s.isEmpty { return attribute(legacy) ?? v }
                return v
            }
            return attribute(legacy)
        }
        func text(_ v: Any?) -> String {
            switch v {
            case let s as String: return s
            case let r as NSAccessibility.Role: return r.rawValue
            case let n as NSNumber: return n.stringValue
            case nil: return ""
            case let other?: return "\(other)"
            }
        }
        var label = text(value("accessibilityLabel", .description))
        if label.isEmpty { label = text(attribute(.title)) }
        let selected = (value("isAccessibilitySelected", .selected) as? Bool)
            ?? (value("isAccessibilitySelected", .selected) as? NSNumber)?.boolValue ?? false
        out.append(Node(role: text(value("accessibilityRole", .role)), label: label,
                        value: text(value("accessibilityValue", .value)),
                        identifier: text(value("accessibilityIdentifier", .identifier)),
                        isSelected: selected))
        let children = (value("accessibilityChildren", .children) as? [Any]) ?? []
        for child in children {
            collect(child, into: &out, depth: depth + 1)
        }
    }
}

/// A view kept hosted across reads, so a test can change the data under a
/// page and see whether the page shows it — `AccessibilityTree.nodes(of:)`
/// builds a fresh host per read, which proves a page renders a state but not
/// that it follows a change. Accessibility stays switched on until `close()`.
@MainActor
final class LiveAccessibilityTree {
    private let window: NSWindow
    private let host: NSView
    private static let readGap: Duration = .milliseconds(400)
    /// How many times a read's own duration the main actor stays free after it.
    private static let idleShare = 3

    init<V: View>(_ view: V, size: CGSize = CGSize(width: 1280, height: 860)) async {
        AccessibilityTree.switchOn()
        try? await Task.sleep(for: .milliseconds(300))
        let host = NSHostingView(rootView: view)
        host.frame = CGRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled, .resizable],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        self.window = window
        self.host = host
    }

    /// Reads the tree until `condition` holds, for up to `timeout`, yielding
    /// the main actor between reads so SwiftUI applies pending changes.
    /// Returns the last read either way; the caller checks it.
    ///
    /// A read walks the whole window's tree on the main actor in one
    /// synchronous burst, so reads are spaced apart by at least `readGap` and
    /// by `idleShare` times as long as the last read took: other suites share
    /// the main actor, and one with a wall-clock wait
    /// (`DebouncedFileEventsTests`, whose debouncer is main-actor isolated)
    /// starved behind reads every 100 ms, and again behind reads every 400 ms
    /// on a slow CI runner where one read took most of the gap. Keeping the
    /// main actor idle three quarters of the time holds however slow a read
    /// gets. The first read waits too, for the change to land.
    @discardableResult
    func nodes(until condition: ([AccessibilityTree.Node]) -> Bool = { _ in true },
               timeout: Duration = .seconds(15)) async -> [AccessibilityTree.Node] {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        var out: [AccessibilityTree.Node] = []
        var lastRead: Duration = .zero
        repeat {
            try? await Task.sleep(for: max(Self.readGap, lastRead * Self.idleShare))
            let started = clock.now
            host.layoutSubtreeIfNeeded()
            host.display()
            out = []
            AccessibilityTree.collect(host, into: &out, depth: 0)
            lastRead = clock.now - started
            if out.count > 1 && condition(out) { return out }
        } while clock.now < deadline
        return out
    }

    func close() {
        window.contentView = nil
        AccessibilityTree.switchOff()
    }
}

extension Array where Element == AccessibilityTree.Node {
    /// Whether any element's label or value contains `text`.
    func shows(_ text: String) -> Bool {
        contains { $0.label.contains(text) || $0.value.contains(text) }
    }

    /// The element with this accessibility identifier.
    func element(_ identifier: String) -> AccessibilityTree.Node? {
        first { $0.identifier == identifier }
    }
}
