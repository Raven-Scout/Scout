import AppKit
import Combine
import SwiftUI
import Testing
import WebKit
@testable import Scout

/// The knowledge map is a web page in a `WKWebView`, so the only way to tell
/// a blank map from a loaded one is to host the real page in a real window
/// and ask it (`window.kbMapState()` in `kb-map.js`). UI audit T1: the map
/// came back empty on a second visit to the page.
@MainActor
@Suite("KB map lifecycle", .serialized)
struct KBMapLifecycleTests {

    /// Stands in for the main window's page switch: the Knowledge Base page,
    /// or another page, rebuilt the way `MainWindowView.detail` rebuilds it.
    final class Nav: ObservableObject {
        @Published var onKB = true
        @Published var pending: String? = nil
    }

    struct Harness: View {
        @ObservedObject var nav: Nav
        let state: AppState
        var body: some View {
            Group {
                if nav.onKB {
                    KnowledgeBaseView(pendingNote: $nav.pending)
                        .environmentObject(state.knowledgeBaseService)
                        .environmentObject(state.knowledgeBaseWriterBox)
                } else {
                    Text("Another page").frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .environmentObject(state.vaultUndo)
        }
    }

    struct MapState: Equatable {
        var mode: String
        var nodes: Int
        var width: Double
        var height: Double
        var frames: Int
    }

    static func webViews(in view: NSView) -> [WKWebView] {
        (view as? WKWebView).map { [$0] } ?? view.subviews.flatMap(webViews(in:))
    }

    /// The state of every map page in the window.
    static func mapStates(in window: NSWindow) async -> [MapState] {
        guard let root = window.contentView else { return [] }
        var out: [MapState] = []
        for web in webViews(in: root) where web.url?.lastPathComponent == "kb-map.html" {
            guard let raw = try? await web.evaluateJavaScript("window.kbMapState ? window.kbMapState() : null"),
                  let d = raw as? [String: Any] else { continue }
            out.append(MapState(mode: d["mode"] as? String ?? "?",
                                nodes: (d["nodes"] as? NSNumber)?.intValue ?? 0,
                                width: (d["width"] as? NSNumber)?.doubleValue ?? 0,
                                height: (d["height"] as? NSNumber)?.doubleValue ?? 0,
                                frames: (d["frames"] as? NSNumber)?.intValue ?? 0))
        }
        return out
    }

    /// The vault map once it holds notes and has drawn them, or nil on timeout.
    static func loadedVaultMap(in window: NSWindow, timeout: Double = 15) async -> MapState? {
        let deadline = Date().addingTimeInterval(timeout)
        var last: [MapState] = []
        while Date() < deadline {
            last = await mapStates(in: window)
            if let full = last.first(where: { $0.mode == "full" }),
               full.nodes > 0, full.width > 0, full.frames > 0 {
                return full
            }
            try? await Task.sleep(for: .milliseconds(100))
        }
        let pages = window.contentView.map { webViews(in: $0).map { "\($0.url?.lastPathComponent ?? "nil") \(Int($0.frame.width))x\(Int($0.frame.height)) window:\($0.window != nil)" } } ?? []
        print("map never loaded; pages: \(pages); states: \(last)")
        return nil
    }

    /// Whether the reader page comes to show `text`.
    static func readerText(in window: NSWindow, containing text: String, timeout: Double = 10) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        var last = ""
        while Date() < deadline {
            if let root = window.contentView {
                for web in webViews(in: root) where web.url?.lastPathComponent == "kb-reader.html" {
                    if let raw = try? await web.evaluateJavaScript("window.kbReaderState ? window.kbReaderState().text : ''"),
                       let shown = raw as? String {
                        last = shown
                        if shown.contains(text) { return true }
                    }
                }
            }
            try? await Task.sleep(for: .milliseconds(100))
        }
        print("reader showed: \(last.prefix(200))")
        return false
    }

    static func settle(_ seconds: Double = 0.6) async {
        try? await Task.sleep(for: .seconds(seconds))
    }

    /// How long a big vault's map takes to show (UI audit T1: 8–25 s at about
    /// 1,400 notes). Prints a timeline; asserts only that it loads. Local
    /// only: set `TEST_RUNNER_SCOUT_KB_MAP_TIMING=1`.
    @Test("a 1,400-note vault's map: time to load and first paint",
          .enabled(if: ProcessInfo.processInfo.environment["SCOUT_KB_MAP_TIMING"] != nil))
    func bigVaultTiming() async throws {
        var scale = BigVault.Scale.layout
        scale.kbNotes = 1400
        let big = try BigVault(scale: scale); defer { big.tearDown() }
        let svc = big.state.knowledgeBaseService
        let start = Date()
        func ms() -> Int { Int(Date().timeIntervalSince(start) * 1000) }

        let nav = Nav()
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 1059, height: 760),
                              styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: Harness(nav: nav, state: big.state))
        window.orderFrontRegardless()
        defer { window.orderOut(nil); window.contentView = nil }

        var timeline: [String] = []
        var sawTree = false, sawNodes = false
        let deadline = Date().addingTimeInterval(60)
        while Date() < deadline {
            if !sawTree, !svc.tree.isEmpty { sawTree = true; timeline.append("index built \(ms()) ms") }
            if let full = await Self.mapStates(in: window).first(where: { $0.mode == "full" }) {
                if !sawNodes, full.nodes > 0 { sawNodes = true; timeline.append("map got \(full.nodes) notes \(ms()) ms") }
                if full.frames > 0 { timeline.append("first paint \(ms()) ms"); break }
            }
            try? await Task.sleep(for: .milliseconds(50))
        }
        print("KB map timing (1,400 notes): " + timeline.joined(separator: " · "))
        #expect(timeline.contains { $0.hasPrefix("first paint") })

        // A second visit: the layout is cached.
        nav.onKB = false
        await Self.settle()
        let again = Date()
        nav.onKB = true
        _ = await Self.loadedVaultMap(in: window, timeout: 30)
        print("KB map timing (1,400 notes), second visit: first paint \(Int(Date().timeIntervalSince(again) * 1000)) ms")
    }

    @Test("the vault map is drawn on every visit: open a note, keep a draft, back to the map, leave, come back")
    func mapSurvivesLeavingAndReturning() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = vault.state.knowledgeBaseService
        svc.load()
        await svc.reparseAndWait()

        let nav = Nav()
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 1060, height: 760),
                              styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: Harness(nav: nav, state: vault.state))
        window.orderFrontRegardless()
        defer { window.orderOut(nil); window.contentView = nil }

        // First visit.
        #expect(await Self.loadedVaultMap(in: window) != nil, "first visit")

        // A note with an unsaved draft opens in Read mode showing the draft.
        let alex = try #require(svc.tree.flatMap(\.allFiles).first { $0.displayName == "alex" })
        let disk = svc.readFile(alex.url)
        svc.drafts.update(KBDraft(text: "# Alex\n\nAn unsaved paragraph.\n", originalText: disk ?? "",
                                  baselineContents: disk, baselineDate: nil),
                          for: alex.url)
        defer { svc.drafts.discard(for: alex.url) }
        nav.pending = "alex"
        let shown = await Self.readerText(in: window, containing: "An unsaved paragraph")
        #expect(shown, "Read mode shows the restored draft")

        // Leave the page from the note, then come back: the overview again.
        nav.onKB = false
        await Self.settle()
        nav.onKB = true
        #expect(await Self.loadedVaultMap(in: window) != nil, "second visit")

        // Leave from the map and come back once more.
        nav.onKB = false
        await Self.settle()
        nav.onKB = true
        #expect(await Self.loadedVaultMap(in: window) != nil, "third visit")
    }
}
