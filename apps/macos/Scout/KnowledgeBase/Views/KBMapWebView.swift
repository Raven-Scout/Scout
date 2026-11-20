import SwiftUI
import AppKit

/// What the map page is sent on `load`.
nonisolated struct KBMapLoad: Encodable {
    let mode: String
    let focus: String?
    let payload: KBMapPayload
    /// Where each note sat when the vault map last settled, by note id.
    var positions: [String: [Double]] = [:]
    /// The user's open/closed choice for the map's floating panels; a nil
    /// panel follows the map's width.
    var panels: KBMapPanels = KBMapPanels()
}

/// Whether the map's Notes legend and side card are collapsed, as the user
/// last set them (nil: by width). Kept in user defaults.
nonisolated struct KBMapPanels: Codable, Equatable {
    var legend: Bool?
    var card: Bool?

    static let defaultsKey = "kbMapPanels"

    static func load(from defaults: UserDefaults = .standard) -> KBMapPanels {
        guard let data = defaults.data(forKey: defaultsKey),
              let panels = try? JSONDecoder().decode(KBMapPanels.self, from: data) else { return KBMapPanels() }
        return panels
    }

    func save(to defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.defaultsKey) }
    }

    /// The page's `panels` message: each value a bool, or null for "by width".
    init(message body: [String: Any]) {
        legend = body["legend"] as? Bool
        card = body["card"] as? Bool
    }

    init(legend: Bool? = nil, card: Bool? = nil) {
        self.legend = legend
        self.card = card
    }
}

/// The vault map's last settled layout, so the map reopens exactly as it was
/// left — and stays where you know things are. Kept for the app's lifetime,
/// and on disk (`file`, the app's caches folder) so a relaunch opens on a
/// settled map instead of laying a big vault out from scratch (UI audit T1).
@MainActor enum KBMapLayoutCache {
    static var positions: [String: [Double]] = [:]
    /// The on-disk copy; nil keeps it in memory only (tests).
    static var file: URL? = nil
    private static var loadedFrom: URL? = nil

    /// The positions to open with: memory, else the file once.
    static func current() -> [String: [Double]] {
        if positions.isEmpty, let file, loadedFrom != file {
            loadedFrom = file
            positions = read(file)
        }
        return positions
    }

    static func store(_ new: [String: [Double]]) {
        positions = new
        guard let file else { return }
        let data = encode(new)
        Task.detached(priority: .utility) {
            try? FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
                                                     withIntermediateDirectories: true)
            try? data?.write(to: file, options: .atomic)
        }
    }

    /// Finite numbers only: a NaN would make the whole load unencodable and
    /// leave the map blank.
    nonisolated static func sanitized(_ raw: [String: [Double]]) -> [String: [Double]] {
        raw.filter { $0.value.count == 2 && $0.value.allSatisfy(\.isFinite) }
    }

    nonisolated static func encode(_ positions: [String: [Double]]) -> Data? {
        try? JSONEncoder().encode(sanitized(positions))
    }

    nonisolated static func read(_ file: URL) -> [String: [Double]] {
        guard let data = try? Data(contentsOf: file),
              let raw = try? JSONDecoder().decode([String: [Double]].self, from: data) else { return [:] }
        return sanitized(raw)
    }

    /// `~/Library/Caches/Scout/kb-map/<bundle id>/<vault key>.json`, one per
    /// build and vault.
    nonisolated static func defaultFile(scoutDirectory: URL) -> URL? {
        guard let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return nil }
        let bundle = Bundle.main.bundleIdentifier ?? "Scout"
        let key = String(scoutDirectory.standardizedFileURL.path.utf8.reduce(UInt64(14695981039346656037)) {
            ($0 ^ UInt64($1)) &* 1099511628211
        }, radix: 16)
        return caches.appendingPathComponent("Scout/kb-map/\(bundle)/\(key).json")
    }
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
            .onAppear {
                wire()
                // A page already up when the view comes back is told to
                // measure and draw again rather than wait for a frame.
                if bridge.isReady { bridge.send("shown") } else { bridge.reloadIfNotReady() }
            }
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
                KBMapLayoutCache.store(KBMapLayoutCache.sanitized(raw.mapValues { $0.map(\.doubleValue) }))
            case "panels":
                KBMapPanels(message: body).save()
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
                                            positions: center == nil ? KBMapLayoutCache.current() : [:],
                                            panels: KBMapPanels.load()))
    }
}
