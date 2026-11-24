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

    fileprivate static let attributes = ["AXEnhancedUserInterface", "AXManualAccessibility"]
        .map { NSAccessibility.Attribute(rawValue: $0) }

    /// Every element under `view`, depth first.
    ///
    /// `async` because inside the running test-host app SwiftUI picks up the
    /// switch, and fills a new hosting view's tree, on a later main-run-loop
    /// turn. Awaiting a short sleep yields the main actor so that turn
    /// happens, without re-entering the run loop (which crashes the host).
    /// The switch is turned off again before returning, so other suites run
    /// with the host as they always did.
    static func nodes<V: View>(of view: V, size: CGSize = CGSize(width: 900, height: 700)) async -> [Node] {
        for attribute in attributes { NSApp.accessibilitySetValue(true, forAttribute: attribute) }
        defer { for attribute in attributes { NSApp.accessibilitySetValue(false, forAttribute: attribute) } }
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

    init<V: View>(_ view: V, size: CGSize = CGSize(width: 1280, height: 860)) async {
        for attribute in AccessibilityTree.attributes { NSApp.accessibilitySetValue(true, forAttribute: attribute) }
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
    @discardableResult
    func nodes(until condition: ([AccessibilityTree.Node]) -> Bool = { _ in true },
               timeout: Duration = .seconds(5)) async -> [AccessibilityTree.Node] {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        var out: [AccessibilityTree.Node] = []
        repeat {
            host.layoutSubtreeIfNeeded()
            host.display()
            out = []
            AccessibilityTree.collect(host, into: &out, depth: 0)
            if out.count > 1 && condition(out) { return out }
            try? await Task.sleep(for: .milliseconds(100))
        } while clock.now < deadline
        return out
    }

    func close() {
        window.contentView = nil
        for attribute in AccessibilityTree.attributes { NSApp.accessibilitySetValue(false, forAttribute: attribute) }
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
