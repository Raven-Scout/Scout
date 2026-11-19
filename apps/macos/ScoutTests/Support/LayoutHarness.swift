import AppKit
import SwiftUI
@testable import Scout

/// Window sizes the layout tests check (UI audit card C6): the main window's
/// minimum (`MainWindowSize`, which `ScoutApp` enforces), the old 1100 pt
/// minimum, a 13" laptop window, and a large display.
enum LayoutSize {
    static let windowWidths: [CGFloat] = [MainWindowSize.minWidth, 1100, 1280, 1600]
    static let minimumWindowHeight: CGFloat = MainWindowSize.minHeight

    /// Sidebar column (`navigationSplitViewColumnWidth` ideal 220) plus its
    /// divider: what a page gets of the window's width.
    static let sidebarWidth: CGFloat = 221
    /// Title bar and toolbar (52) plus the status bar strip (26).
    static let windowChrome: CGFloat = 78

    static func pageWidth(window: CGFloat) -> CGFloat { window - sidebarWidth }
    static var minimumPageHeight: CGFloat { minimumWindowHeight - windowChrome }
}

/// Measures views the way their parent lays them out, and writes PNG
/// snapshots for a person to look at.
///
/// The measurement is the gate: `minimumWidth` proposes a width far below
/// any real window, so a view answers with the narrowest it can lay out.
/// A row that clips at a real window size is one whose minimum is wider than
/// the page — that is what pushed the Action Items filter row off the left
/// edge. Snapshots are for review only: they are written only when the test
/// process has `SCOUT_LAYOUT_SNAPSHOTS` set to a folder
/// (`TEST_RUNNER_SCOUT_LAYOUT_SNAPSHOTS=<dir> xcodebuild test …`).
@MainActor
enum LayoutHarness {

    /// The size `view` picks when offered `proposal`.
    static func size<V: View>(of view: V, proposing proposal: CGSize) -> CGSize {
        let controller = NSHostingController(rootView: view)
        return controller.sizeThatFits(in: proposal)
    }

    /// The narrowest `view` can lay out, at `height`.
    static func minimumWidth<V: View>(of view: V, height: CGFloat = 2000) -> CGFloat {
        size(of: view, proposing: CGSize(width: 1, height: height)).width
    }

    /// The height `view` takes at `width` with unlimited height.
    static func height<V: View>(of view: V, width: CGFloat) -> CGFloat {
        size(of: view, proposing: CGSize(width: width, height: 10_000)).height
    }

    static var snapshotDirectory: URL? {
        guard let raw = ProcessInfo.processInfo.environment["SCOUT_LAYOUT_SNAPSHOTS"], !raw.isEmpty else { return nil }
        return URL(fileURLWithPath: raw, isDirectory: true)
    }

    /// Render `view` in an off-screen window of `size` and write
    /// `<name>-<width>x<height>-<light|dark>.png`, in both appearances. A
    /// no-op unless `SCOUT_LAYOUT_SNAPSHOTS` is set.
    static func snapshot<V: View>(_ view: V, size: CGSize, name: String) {
        guard let directory = snapshotDirectory else { return }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        for dark in [false, true] {
            let host = NSHostingView(rootView: view)
            host.frame = CGRect(origin: .zero, size: size)
            let window = NSWindow(contentRect: host.frame, styleMask: [.titled, .resizable],
                                  backing: .buffered, defer: true)
            window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            host.displayIfNeeded()
            if let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
                host.cacheDisplay(in: host.bounds, to: rep)
                let file = directory.appendingPathComponent(
                    "\(name)-\(Int(size.width))x\(Int(size.height))-\(dark ? "dark" : "light").png")
                try? rep.representation(using: .png, properties: [:])?.write(to: file)
            }
            window.contentView = nil
        }
    }
}
