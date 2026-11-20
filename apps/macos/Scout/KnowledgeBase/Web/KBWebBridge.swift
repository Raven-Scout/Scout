import SwiftUI
import Combine
import AppKit
import WebKit

/// A bundled web page (the knowledge map or the note reader) hosted in a
/// `WKWebView`, and the message channel between it and Swift.
///
/// The page and every script it loads ship inside the app (see
/// `Web/KB-WEB-LICENSES.md`); nothing is fetched from the network. The page
/// talks back through `window.webkit.messageHandlers.scout`, as
/// `{type, …payload}`; Swift talks to it through `send`, which calls
/// `window.scoutReceive(message)`. Messages sent before the page says `ready`
/// are queued and delivered in order once it does.
@MainActor
final class KBWebBridge: NSObject, ObservableObject {
    let webView: WKWebView
    /// Called for every message the page posts, with its `type` and body.
    var onMessage: ((String, [String: Any]) -> Void)?
    /// Called for a link the page navigated to by itself (a plain `<a>` the
    /// page's own click handling didn't catch). Never loaded in the view.
    var onExternalLink: ((URL) -> Void)?

    private(set) var isReady = false
    private var queue: [String] = []
    /// Message types whose latest copy is sent again if the page has to be
    /// reloaded (its web process died), so it comes back showing what it
    /// showed instead of blank. The map marks `load`.
    var replayOnReload: Set<String> = []
    private var latest: [String: String] = [:]
    private let page: String

    /// `page` is the bundled HTML file's name without extension (`kb-map`).
    init(page: String) {
        self.page = page
        let config = WKWebViewConfiguration()
        let content = WKUserContentController()
        content.addUserScript(WKUserScript(source: KBWebTheme.injectionScript(),
                                           injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController = content
        config.websiteDataStore = .nonPersistent()
        config.preferences.isElementFullscreenEnabled = false
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        content.add(WeakMessageHandler(self), name: "scout")
        webView.navigationDelegate = self
        webView.allowsMagnification = false
        webView.allowsBackForwardNavigationGestures = false
        // Let the SwiftUI paper show through until (and around) the page.
        webView.setValue(false, forKey: "drawsBackground")
        #if DEBUG
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        #endif
        load()
    }

    /// Reload the page if it hasn't said `ready` within `timeout` of being
    /// shown: a page that never came up would otherwise leave its pane blank
    /// for good (UI audit T1). Messages sent meanwhile stay queued for it.
    func reloadIfNotReady(after timeout: Duration = .seconds(4)) {
        let started = loads
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: timeout)
            guard let self, !self.isReady, self.loads == started, self.webView.window != nil else { return }
            NSLog("[KBWeb \(self.page)] not ready after \(timeout); reloading")
            self.load()
        }
    }

    /// How many times the page has been loaded; a reload already under way
    /// isn't started again.
    private var loads = 0

    private func load() {
        loads += 1
        guard let url = Bundle.main.url(forResource: page, withExtension: "html") else {
            assertionFailure("Missing bundled page \(page).html")
            return
        }
        webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }

    /// Send `{type, …payload}` to the page.
    func send(_ type: String, _ payload: [String: Any] = [:]) {
        var message = payload
        message["type"] = type
        guard JSONSerialization.isValidJSONObject(message),
              let data = try? JSONSerialization.data(withJSONObject: message),
              let json = String(data: data, encoding: .utf8) else { return }
        deliver(json, type: type)
    }

    /// Send an `Encodable` payload; it arrives as `message.data`.
    func send<T: Encodable>(_ type: String, data: T) {
        guard let encoded = try? JSONEncoder().encode(data),
              let json = String(data: encoded, encoding: .utf8),
              let typeData = try? JSONEncoder().encode(type),
              let typeJSON = String(data: typeData, encoding: .utf8) else { return }
        deliver("{\"type\":\(typeJSON),\"data\":\(json)}", type: type)
    }

    private func deliver(_ json: String, type: String) {
        if replayOnReload.contains(type) { latest[type] = json }
        guard isReady else { queue.append(json); return }
        webView.evaluateJavaScript("window.scoutReceive(\(json));", completionHandler: nil)
    }

    fileprivate func receive(_ body: Any) {
        guard let dict = body as? [String: Any], let type = dict["type"] as? String else { return }
        if type == "ready" {
            isReady = true
            let pending = queue
            queue.removeAll()
            for json in pending {
                webView.evaluateJavaScript("window.scoutReceive(\(json));", completionHandler: nil)
            }
        }
        if type == "jsError" {
            NSLog("[KBWeb \(page)] \(dict["message"] ?? "") @ \(dict["source"] ?? ""):\(dict["line"] ?? "")")
        }
        onMessage?(type, dict)
    }
}

extension KBWebBridge: WKNavigationDelegate {
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        // Only the bundled page itself may load in the view.
        if let url = navigationAction.request.url, url.isFileURL, navigationAction.navigationType == .other {
            decisionHandler(.allow)
            return
        }
        if let url = navigationAction.request.url { onExternalLink?(url) }
        decisionHandler(.cancel)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        // The page is gone; reload it, then replay what it was showing.
        isReady = false
        queue = Array(latest.values)
        load()
    }
}

/// `WKUserContentController` holds its handlers strongly; this breaks the
/// bridge → web view → controller → bridge cycle.
private final class WeakMessageHandler: NSObject, WKScriptMessageHandler {
    weak var bridge: KBWebBridge?
    init(_ bridge: KBWebBridge) { self.bridge = bridge }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let body = message.body
        MainActor.assumeIsolated { bridge?.receive(body) }
    }
}

/// Puts a bridge's web view in a SwiftUI hierarchy. The bridge (and so the
/// page) outlives SwiftUI's view updates; owners hold it in `@StateObject`.
struct KBWebViewHost: NSViewRepresentable {
    let bridge: KBWebBridge
    func makeNSView(context: Context) -> WKWebView { bridge.webView }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

/// The design system's colours and type scale as CSS custom properties, so
/// the web pages draw with the same tokens as the SwiftUI around them. Light
/// values on `:root`, dark ones under `prefers-color-scheme: dark` (which a
/// `WKWebView` takes from its window's appearance), and the Increase Contrast
/// values under `prefers-contrast: more`.
enum KBWebTheme {
    /// Token name → swatch. Group colours are `--g-<group>`.
    @MainActor static var tokens: [(String, DS.Swatch)] {
        let base: [(String, DS.Swatch)] = [
            ("paper", DS.Palette.paperBase), ("paper-sunk", DS.Palette.paperSunk),
            ("paper-raised", DS.Palette.paperRaised),
            ("ink-1", DS.Palette.inkP1), ("ink-2", DS.Palette.inkP2),
            ("ink-3", DS.Palette.inkP3), ("ink-4", DS.Palette.inkP4),
            ("ink-decorative", DS.Palette.inkDecorative),
            ("rule-soft", DS.Palette.ruleSoft), ("rule-hard", DS.Palette.ruleHard),
            ("accent-fill", DS.Palette.accentFill), ("accent-ink", DS.Palette.accentInk),
            ("accent-wash", DS.Palette.accentWash), ("accent-on-fill", DS.Palette.accentOnFill),
            ("ok", DS.Palette.statusOk), ("warn", DS.Palette.statusWarn), ("err", DS.Palette.statusErr),
        ]
        return base + KBEntityGroup.allCases.map { ("g-\($0.rawValue)", $0.swatch) }
    }

    nonisolated static func css(_ rgb: DS.RGB) -> String {
        func c(_ v: Double) -> Int { Int((min(max(v, 0), 1) * 255).rounded()) }
        if rgb.a >= 1 { return "rgb(\(c(rgb.r)), \(c(rgb.g)), \(c(rgb.b)))" }
        return "rgba(\(c(rgb.r)), \(c(rgb.g)), \(c(rgb.b)), \(String(format: "%.3f", rgb.a)))"
    }

    /// The stylesheet: one block per appearance.
    @MainActor static func stylesheet(textScale: CGFloat = TextScale.shared.factor) -> String {
        func block(_ appearance: DS.Appearance) -> String {
            tokens.map { "--\($0.0): \(css($0.1.resolved(appearance)));" }.joined(separator: " ")
        }
        return """
        :root { color-scheme: light dark; --text-scale: \(textScale); \(block(.light)) }
        @media (prefers-color-scheme: dark) { :root { \(block(.dark)) } }
        @media (prefers-contrast: more) { :root { \(block(.lightIncreasedContrast)) } }
        @media (prefers-color-scheme: dark) and (prefers-contrast: more) { :root { \(block(.darkIncreasedContrast)) } }
        """
    }

    /// A document-start script that installs the stylesheet before the page's
    /// own CSS runs, so the first paint already has the right colours.
    @MainActor static func injectionScript() -> String {
        let css = stylesheet()
        let literal = (try? JSONEncoder().encode(css)).flatMap { String(data: $0, encoding: .utf8) } ?? "\"\""
        return """
        (function(){var s=document.createElement('style');s.id='scout-theme';s.textContent=\(literal);
        (document.head||document.documentElement).appendChild(s);})();
        """
    }
}
