import SwiftUI
import AppKit
import Combine
import WebKit

/// Find in the open note (⌘F, UI audit #77). Rich and Source are text views
/// and get the system find bar, with ⌘G, ⇧⌘G, Replace and a match count. Read
/// is the `kb-reader` web page (#677), which has no find bar of its own, so it
/// gets `KBReaderFindBar` over `WKWebView.find`.
@MainActor
final class KBFindController: ObservableObject {
    /// The Rich or Source editor's text view, while one is on screen.
    weak var textView: NSTextView?
    /// The Read page's web view, while it is on screen.
    weak var webView: WKWebView?
    /// Whether Read mode's find bar shows.
    @Published var readerBarVisible = false
    /// Bumped by every ⌘F, so an open bar takes focus again.
    @Published private(set) var focusRequests = 0

    /// ⌘F: the reader's bar in Read mode, else the text view's find bar.
    func show(inReader: Bool) {
        if inReader {
            readerBarVisible = true
            focusRequests += 1
            return
        }
        guard let textView else { return }
        textView.window?.makeFirstResponder(textView)
        Self.perform(.showFindInterface, on: textView)
    }

    /// Runs a find-bar action on a text view, as Edit ▸ Find's items do.
    static func perform(_ action: NSTextFinder.Action, on textView: NSTextView) {
        let item = NSMenuItem()
        item.tag = action.rawValue
        textView.performTextFinderAction(item)
    }

    /// The next (or previous) match in the reader; false when there is none.
    /// `fromTop` starts over, for a query that has just changed.
    func findInReader(_ query: String, backwards: Bool = false, fromTop: Bool = false) async -> Bool {
        guard let webView, !query.isEmpty else { return false }
        if fromTop {
            // Ends in `true`: an `undefined` result would come back as an error.
            _ = try? await webView.evaluateJavaScript("window.getSelection().removeAllRanges(); true")
        }
        let config = WKFindConfiguration()
        config.backwards = backwards
        config.caseSensitive = false
        config.wraps = true
        return await withCheckedContinuation { continuation in
            webView.find(query, configuration: config) { result in
                continuation.resume(returning: result.matchFound)
            }
        }
    }
}

/// Read mode's find bar: type to find, Return or ⌘G for the next match,
/// ⇧Return or ⇧⌘G for the previous one, Esc or Done to close.
struct KBReaderFindBar: View {
    @ObservedObject var controller: KBFindController

    @State private var query = ""
    @State private var notFound = false
    @State private var searchTask: Task<Void, Never>?
    @FocusState private var fieldFocused: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").font(DS.font(.caption)).foregroundStyle(DS.Ink.p4)
            TextField("Find in note", text: $query)
                .textFieldStyle(.plain)
                .font(DS.font(.label)).foregroundStyle(DS.Ink.p1)
                .focused($fieldFocused)
                .onSubmit { step(backwards: NSEvent.modifierFlags.contains(.shift)) }
                .onExitCommand { close() }
                .accessibilityLabel("Find in note")
            if notFound {
                Text("Not found").font(DS.font(.caption)).foregroundStyle(DS.Status.warn)
            }
            Button { step(backwards: true) } label: {
                Image(systemName: "chevron.up").font(DS.font(.caption, weight: .semibold))
            }
            .buttonStyle(.plainHit) // ds-guard: hit area (an icon button)
            .keyboardShortcut(AppShortcut.findPrevious)
            .help(ShortcutLabel.help("Previous match", AppShortcut.findPrevious))
            .accessibilityLabel("Previous match")
            .disabled(query.isEmpty)
            Button { step(backwards: false) } label: {
                Image(systemName: "chevron.down").font(DS.font(.caption, weight: .semibold))
            }
            .buttonStyle(.plainHit) // ds-guard: hit area (an icon button)
            .keyboardShortcut(AppShortcut.findNext)
            .help(ShortcutLabel.help("Next match", AppShortcut.findNext))
            .accessibilityLabel("Next match")
            .disabled(query.isEmpty)
            Button("Done") { close() }
                .buttonStyle(.ds(.secondary, size: .small))
        }
        .foregroundStyle(DS.Ink.p3)
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(DS.Paper.sunk)
        .overlay(alignment: .bottom) { EditorialRule() }
        .onAppear { fieldFocused = true }
        .onChange(of: controller.focusRequests) { _, _ in fieldFocused = true }
        .onChange(of: query) { _, new in
            searchTask?.cancel()
            searchTask = Task {
                let found = await controller.findInReader(new, fromTop: true)
                guard !Task.isCancelled else { return }
                notFound = !new.isEmpty && !found
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Find in note")
    }

    private func step(backwards: Bool) {
        let q = query
        searchTask?.cancel()
        searchTask = Task {
            let found = await controller.findInReader(q, backwards: backwards)
            guard !Task.isCancelled else { return }
            notFound = !q.isEmpty && !found
        }
    }

    private func close() {
        searchTask?.cancel()
        controller.readerBarVisible = false
    }
}
