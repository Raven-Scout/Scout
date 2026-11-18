import SwiftUI
import AppKit

/// What the map page is sent on `load`.
nonisolated struct KBMapLoad: Encodable {
    let mode: String
    let focus: String?
    let payload: KBMapPayload
    /// Where each note sat when the vault map last settled, by note id.
    var positions: [String: [Double]] = [:]
}

/// The vault map's last settled layout, kept for the app's lifetime so the
/// map reopens exactly as it was left — and stays where you know things are.
@MainActor enum KBMapLayoutCache {
    static var positions: [String: [Double]] = [:]
}

/// The knowledge map (`kb-map.html`): the whole vault on the overview, or one
/// note's neighbourhood in the note's side panel. Rebuilds whenever the
/// service reparses; the page keeps every node where it was, so a reparse
/// doesn't throw the layout away.
struct KBMapWebView: View {
    enum Mode: Equatable {
        /// Every note in the vault.
        case vault
        /// `center` and the notes within two links of it.
        case local(center: String)
    }

    @ObservedObject var service: KnowledgeBaseService
    let mode: Mode
    /// A note to fly to and select once the map has loaded (vault mode).
    var focus: String? = nil
    /// Open a note in the editor.
    let onOpen: (String) -> Void

    @StateObject private var bridge = KBWebBridge(page: "kb-map")
    /// The `focus` already handed to the page. A reload after a reparse must
    /// not fly the camera back to a note the user has since moved on from.
    @State private var deliveredFocus: String? = nil

    var body: some View {
        KBWebViewHost(bridge: bridge)
            .accessibilityLabel(mode == .vault ? "Knowledge map" : "Link graph")
            .onAppear { wire() }
            .task(id: LoadKey(mode: mode, generation: service.indexGeneration)) { await sendLoad() }
            .onChange(of: focus) { _, new in
                guard let new, new != deliveredFocus else { return }
                deliveredFocus = new
                bridge.send("focus", ["id": new])
            }
    }

    private struct LoadKey: Equatable {
        let mode: Mode
        let generation: Int
    }

    private func wire() {
        // Capture the pieces, not `self`: the bridge outlives this struct and
        // must not keep itself alive through its own handler.
        let open = onOpen
        bridge.replayOnReload = ["load"]
        bridge.onMessage = { [weak bridge = self.bridge, weak service = self.service] type, body in
            switch type {
            case "open":
                if let id = body["id"] as? String { open(id) }
            case "positions":
                guard let raw = body["positions"] as? [String: [NSNumber]] else { return }
                KBMapLayoutCache.positions = raw.mapValues { $0.map(\.doubleValue) }
            case "details":
                guard let id = body["id"] as? String, let service else { return }
                bridge?.send("details", ["id": id, "excerpt": service.summary(for: id)])
            default:
                break
            }
        }
    }

    private func sendLoad() async {
        let graph: KBGraph
        let center: String?
        switch mode {
        case .vault:
            graph = service.fullGraph()
            center = nil
        case .local(let c):
            graph = service.localGraph(around: c, depth: 2, maxNodes: 40)
            center = c
        }
        let urls = Dictionary(uniqueKeysWithValues: service.tree.flatMap(\.allFiles).map { ($0.relativePath, $0.url) })
        let ids = graph.nodes.map(\.id)
        // One stat per note — off the main actor, a vault has ~1,000+.
        let modified = await Task.detached(priority: .userInitiated) { () -> [String: Date] in
            var out: [String: Date] = [:]
            for id in ids {
                guard let url = urls[id],
                      let date = try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
                else { continue }
                out[id] = date
            }
            return out
        }.value
        guard !Task.isCancelled else { return }
        let payload = KBMapPayload.build(from: graph, center: center, modified: modified)
        let newFocus = center == nil && focus != deliveredFocus ? focus : nil
        if newFocus != nil { deliveredFocus = newFocus }
        bridge.send("load", data: KBMapLoad(mode: center == nil ? "full" : "local",
                                            focus: newFocus,
                                            payload: payload,
                                            positions: center == nil ? KBMapLayoutCache.positions : [:]))
    }
}
