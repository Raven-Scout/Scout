import Combine
import Foundation
import os

/// One row of `scoutctl kb classify --reader owner --json` (#453). The engine
/// prints more fields (`visibility_source`, `federation_scope_declared`, …);
/// the badge needs only these, so the rest is ignored. A reader the engine
/// denies gets `{"path": …, "denied": true}` with no `visibility`.
nonisolated struct KBPrivacyLabel: Decodable, Equatable, Sendable {
    let path: String
    let visibility: String?
    let localOnly: Bool?
    let denied: Bool?

    enum CodingKeys: String, CodingKey {
        case path, visibility, denied
        case localOnly = "local_only"
    }

    static func decodeList(_ data: Data) throws -> [KBPrivacyLabel] {
        try JSONDecoder().decode([KBPrivacyLabel].self, from: data)
    }
}

/// What the KB tree shows next to a file for its privacy label.
nonisolated enum KBPrivacyBadge: Equatable, Sendable {
    case none
    case locked

    /// `private` → a lock. `normal`, a denial or no label at all → nothing
    /// (no label is never a claim that the file is normal; it just has no badge).
    /// The engine only prints `normal`/`private`; any other value reads as
    /// private, the way the engine itself reads an odd `visibility:` value.
    static func badge(for label: KBPrivacyLabel?) -> KBPrivacyBadge {
        guard let label, label.denied != true, let visibility = label.visibility else { return .none }
        let value = visibility.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return value == "normal" ? .none : .locked
    }

    var systemImage: String? { self == .locked ? "lock.fill" : nil }
}

/// What a file's label is cached by: when any of these changes, the file is
/// classified again.
nonisolated struct KBFileStamp: Equatable, Hashable, Sendable {
    let modified: Date
    let size: Int64
    let inode: UInt64

    static func of(_ url: URL) -> KBFileStamp? {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              let modified = attrs[.modificationDate] as? Date else { return nil }
        let size = (attrs[.size] as? NSNumber)?.int64Value ?? -1
        let inode = (attrs[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
        return KBFileStamp(modified: modified, size: size, inode: inode)
    }
}

/// Privacy labels for the KB tree's lock badge, read through the engine's one
/// read gate (`scoutctl kb classify`) rather than parsed here: the engine owns
/// the rules (path defaults, the vault overlay, fail-closed frontmatter).
///
/// Only the files the tree shows are classified, in batches (one process per
/// `batchSize` files, never one per file), and each label is cached by the
/// file's stamp (modification date, size, inode), so a reparse only asks
/// about files that changed. A changed `scout-config.yaml` (its `privacy:`
/// overlay can relabel any file) drops the whole cache. When `scoutctl` fails,
/// those files show no badge, the exit code is logged, and the next refresh
/// tries again; an engine without `kb classify` (exit 2, "No such command")
/// isn't asked again until the `scoutctl` executable changes.
@MainActor
final class KBPrivacyLabelService: ObservableObject {
    /// Vault-relative paths (`KBNode.relativePath`) whose label is private.
    @Published private(set) var privatePaths: Set<String> = []

    private struct Entry {
        let stamp: KBFileStamp
        let badge: KBPrivacyBadge
    }

    private static let log = Logger(subsystem: "com.scout.Scout", category: "KBPrivacy")

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]
    private let vault: URL
    private let batchSize: Int
    private let stamp: (URL) -> KBFileStamp?
    private var cache: [String: Entry] = [:]
    /// Paths a running refresh is classifying; an overlapping refresh (the
    /// tree's task restarts when a folder opens mid-call) leaves them to it.
    private var inFlight: Set<String> = []
    /// Stamp of `scout-config.yaml` when the cache was filled.
    private var configStamp: KBFileStamp?
    /// Set after `kb classify` exits 2 / "No such command". Nothing is asked
    /// again until the `scoutctl` executable's stamp differs from `refusedStamp`.
    private var refused = false
    private var refusedStamp: KBFileStamp?

    init(
        scoutctl: URL,
        runner: any ProcessRunner,
        argumentsPrefix: [String] = [],
        vault: URL,
        batchSize: Int = 200,
        stamp: @escaping (URL) -> KBFileStamp? = KBFileStamp.of
    ) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
        self.vault = vault
        self.batchSize = max(1, batchSize)
        self.stamp = stamp
    }

    nonisolated static func classifyArguments(vault: URL, paths: [String]) -> [String] {
        ["kb", "classify", "--reader", "owner", "--json", "--repo", vault.path, "--"] + paths
    }

    /// Files under the tree's expanded folders (top-level files included).
    nonisolated static func visibleFiles(in nodes: [KBNode], expanded: Set<String>) -> [KBNode] {
        nodes.flatMap { node -> [KBNode] in
            if !node.isDirectory { return [node] }
            return expanded.contains(node.relativePath) ? visibleFiles(in: node.children, expanded: expanded) : []
        }
    }

    func badge(for relativePath: String) -> KBPrivacyBadge {
        cache[relativePath]?.badge ?? .none
    }

    /// Classify every file in `files` whose stamp changed since its last label.
    func refresh(_ files: [KBNode]) async {
        let config = stamp(vault.appendingPathComponent("scout-config.yaml"))
        if config != configStamp {
            cache.removeAll()
            configStamp = config
        }
        if refused {
            guard stamp(scoutctl) != refusedStamp else {
                publish()
                return
            }
            refused = false  // a different engine: try again
        }
        var stale: [(path: String, stamp: KBFileStamp)] = []
        for node in files where !node.isDirectory {
            guard let current = stamp(node.url) else {
                cache[node.relativePath] = nil
                continue
            }
            if cache[node.relativePath]?.stamp != current, !inFlight.contains(node.relativePath) {
                stale.append((node.relativePath, current))
            }
        }
        inFlight.formUnion(stale.map(\.path))
        var start = 0
        while start < stale.count {
            let end = min(start + batchSize, stale.count)
            await classify(Array(stale[start..<end]))
            inFlight.subtract(stale[start..<end].map(\.path))
            start = end
        }
        publish()
    }

    private func classify(_ batch: [(path: String, stamp: KBFileStamp)]) async {
        let arguments = argumentsPrefix + Self.classifyArguments(vault: vault, paths: batch.map(\.path))
        do {
            let result = try await runner.run(
                executable: scoutctl, arguments: arguments, environment: [:], workingDirectory: vault)
            guard result.exitCode == 0 else {
                // stderr may name a vault path: keep it out of the public log.
                let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
                Self.log.error("kb classify exited \(result.exitCode): \(stderr, privacy: .private)")
                if Self.isUnsupported(exitCode: result.exitCode, stderr: stderr) {
                    refused = true
                    refusedStamp = stamp(scoutctl)
                }
                return
            }
            let labels = try KBPrivacyLabel.decodeList(result.stdout)
            guard labels.count == batch.count else {
                Self.log.error("kb classify returned \(labels.count) labels for \(batch.count) files")
                return
            }
            // `kb classify` prints one label per path, in the order given.
            for (item, label) in zip(batch, labels) {
                cache[item.path] = Entry(stamp: item.stamp, badge: KBPrivacyBadge.badge(for: label))
            }
        } catch {
            Self.log.error("kb classify failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    /// An engine without `kb classify`, or one that refuses the call outright:
    /// asking again before the engine changes would fail the same way.
    nonisolated static func isUnsupported(exitCode: Int32, stderr: String) -> Bool {
        exitCode == 2 || stderr.contains("No such command")
    }

    private func publish() {
        let locked = Set(cache.compactMap { $0.value.badge == .locked ? $0.key : nil })
        if locked != privatePaths { privatePaths = locked }
    }
}
