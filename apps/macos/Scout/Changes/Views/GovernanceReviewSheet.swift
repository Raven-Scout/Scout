import Combine
import Foundation
import SwiftUI

/// What the review sheet knows and does, apart from drawing it: the diff it read, the hash it will quote, and
/// what it tells the owner after a decision. The app never edits the pending file; it only reads it to show the
/// diff, and every decision is a `scoutctl` call through `ChangesDecisions`.
@MainActor
final class GovernanceReviewModel: ObservableObject {
    static let editedMessage = "This change was edited after you opened it. Review it again."
    static let noDiffMessage = "Scout couldn't show this change's diff."
    static let invalidMessage = "Scout couldn't send that decision."
    static let goneMessage = "This change is no longer waiting for your OK."
    /// What the engine says when the file changed after the owner saw it (`scoutctl changes approve`).
    nonisolated static let staleMarker = "changed since you saw it"

    @Published private(set) var item: NeedsOKItem
    /// The one fenced diff block of the pending file, or nil when it couldn't be read.
    @Published private(set) var diff: String?
    @Published private(set) var message: String?
    @Published private(set) var busy = false
    /// The item is no longer waiting (decided elsewhere, or gone after a reload).
    @Published private(set) var gone = false
    /// A decision went through; the sheet closes.
    @Published private(set) var finished = false

    private let decisions: ChangesDecisions
    private let service: ChangesService
    private let vault: URL?

    init(item: NeedsOKItem, vaultDirectory: URL?, decisions: ChangesDecisions, service: ChangesService) {
        self.item = item
        self.vault = vaultDirectory
        self.decisions = decisions
        self.service = service
        self.diff = Self.readDiff(vault: vaultDirectory, path: item.path)
    }

    /// You can't approve what you didn't see.
    var canApprove: Bool { diff != nil && !busy && !gone }
    var canReject: Bool { !busy && !gone }

    /// The diff, or why there isn't one.
    var diffMissingMessage: String? { diff == nil ? Self.noDiffMessage : nil }

    func approve() async {
        guard canApprove else { return }
        // The hash covers what the owner saw: read the file once more and send only if it is still that diff.
        guard Self.readDiff(vault: vault, path: item.path) == diff else {
            busy = true
            await service.refresh()
            reload()
            message = Self.editedMessage
            busy = false
            return
        }
        await decide(.approve(govID: item.id, diffHash: item.diffHash))
    }

    func reject() async {
        guard canReject else { return }
        await decide(.reject(govID: item.id))
    }

    private func decide(_ command: ChangesDecisions.Command) async {
        busy = true
        message = nil
        let outcome = await decisions.perform(command)
        switch outcome {
        case .done:
            await service.refresh()
            finished = true
        case .refused(let text) where text.contains(Self.staleMarker):
            await service.refresh()
            reload()
            message = Self.editedMessage
        case .refused(let text):
            message = text
        case .invalid:
            message = Self.invalidMessage
        }
        busy = false
    }

    /// After a refresh: show the item as it is now, with its new diff and hash, or note that it is gone.
    private func reload() {
        guard let now = service.snapshot?.changes.needsOK.first(where: { $0.id == item.id && $0.state == "awaiting-ok" })
        else {
            gone = true
            diff = nil
            return
        }
        item = now
        diff = Self.readDiff(vault: vault, path: now.path)
    }

    // MARK: - Reading the pending file

    /// Read-only. Nil when the path isn't a safe one inside the vault's pending folder or has no single diff block.
    static func readDiff(vault: URL?, path: String) -> String? {
        guard let url = pendingFileURL(vault: vault, path: path), let vault,
              let text = readRegularFile(vault.appendingPathComponent(path)) else { return nil }
        return diffBlock(fromPendingFile: text)
    }

    /// The file's text, refusing a symlink at the final component (O_NOFOLLOW), a non-regular file or one over 1 MB.
    nonisolated static func readRegularFile(_ url: URL) -> String? {
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW)
        guard fd >= 0 else { return nil }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, info.st_size <= 1_000_000,
              let data = try? handle.readToEnd() else { return nil }
        return String(data: data, encoding: .utf8)
    }

    nonisolated static let pendingFolder = "governance-pending"

    /// `<vault>/<path>` only when `path` is relative with no `..` component and the symlink-resolved file lies
    /// inside the vault's resolved governance-pending folder. The engine supplies `path`, so it is not trusted.
    nonisolated static func pendingFileURL(vault: URL?, path: String) -> URL? {
        guard let vault, !path.isEmpty, !path.hasPrefix("/"), !path.hasPrefix("~"), !path.contains("\0") else { return nil }
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        guard !parts.contains(".."), !parts.contains(where: { $0.isEmpty }) else { return nil }
        let folder = vault.appendingPathComponent(pendingFolder, isDirectory: true).resolvingSymlinksInPath()
        let file = vault.appendingPathComponent(path).resolvingSymlinksInPath()
        let prefix = folder.path.hasSuffix("/") ? folder.path : folder.path + "/"
        guard file.path.hasPrefix(prefix), file.path.count > prefix.count else { return nil }
        return file
    }

    /// The content of the one fenced `diff` block of a pending file (the part the approval hash covers), or nil
    /// when there is none or more than one. Mirrors the engine's rule in `scout/changes/governance.py`.
    nonisolated static func diffBlock(fromPendingFile text: String) -> String? {
        var blocks: [String] = []
        var current: [String]?
        for line in text.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: CharacterSet(charactersIn: " \t"))
            if current == nil {
                if line.hasPrefix("```"), trimmed.dropFirst(3).trimmingCharacters(in: CharacterSet(charactersIn: " \t")) == "diff",
                   trimmed.hasPrefix("```") { current = [] }
            } else if line.hasPrefix("```"), trimmed == "```" {
                blocks.append((current ?? []).joined(separator: "\n") + "\n")
                current = nil
            } else {
                current?.append(line)
            }
        }
        return blocks.count == 1 ? blocks[0] : nil
    }
}

/// Reviews one change waiting for the owner's OK: what it is, why, what it touches, and the exact diff. Approving
/// sends the hash of the diff that was shown.
struct GovernanceReviewSheet: View {
    @StateObject private var model: GovernanceReviewModel
    var onClose: () -> Void = {}

    init(item: NeedsOKItem, vaultDirectory: URL?, decisions: ChangesDecisions, service: ChangesService,
         onClose: @escaping () -> Void = {}) {
        _model = StateObject(wrappedValue: GovernanceReviewModel(
            item: item, vaultDirectory: vaultDirectory, decisions: decisions, service: service))
        self.onClose = onClose
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(verbatim: model.item.summary)
                .dsFont(.headline, .serif, weight: .semibold)
                .foregroundStyle(DS.Ink.p1)
                .fixedSize(horizontal: false, vertical: true)
            if !model.item.why.isEmpty {
                Text(verbatim: model.item.why)
                    .font(DS.font(.body))
                    .foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            facts
            diffView
            if let message = model.message {
                Text(verbatim: message)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Status.err)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Not done: \(message)")
            }
            if model.gone {
                Text(Self.goneLine)
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p2)
            }
            HStack(spacing: 10) {
                Button("Close") { onClose() }
                    .buttonStyle(.dsSecondary)
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Reject") { Task { await model.reject() } }
                    .buttonStyle(.dsSecondary)
                    .disabled(!model.canReject)
                    .accessibilityHint("Turns this change down. Nothing is applied.")
                Button(model.busy ? "Working…" : "Approve") { Task { await model.approve() } }
                    .buttonStyle(.dsPrimary)
                    .disabled(!model.canApprove)
                    .accessibilityHint("Approves exactly the diff shown here.")
            }
        }
        .padding(20)
        .frame(minWidth: 560, idealWidth: 640, minHeight: 440, idealHeight: 560)
        .onChange(of: model.finished) { _, done in if done { onClose() } }
    }

    static let goneLine = GovernanceReviewModel.goneMessage

    static func governanceLine(_ governance: Bool) -> String {
        governance
            ? "Applying this is a governance change: it changes what Scout may do on its own."
            : "Applying this is not a governance change."
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(Self.governanceLine(model.item.governance))
                .font(DS.font(.label, weight: .semibold))
                .foregroundStyle(model.item.governance ? DS.Attention.you : DS.Ink.p2)
            if !model.item.targets.isEmpty {
                Text(verbatim: "It changes: " + model.item.targets.joined(separator: ", "))
                    .font(DS.font(.caption))
                    .foregroundStyle(DS.Ink.p3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var diffView: some View {
        if let diff = model.diff {
            ScrollView([.vertical, .horizontal]) {
                Text(verbatim: diff)
                    .font(DS.font(.caption, .mono))
                    .foregroundStyle(DS.Ink.p1)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
            }
            .frame(maxHeight: .infinity)
            .background(DS.Paper.sunk)
            .accessibilityLabel("The change, as a diff")
        } else {
            Text(GovernanceReviewModel.noDiffMessage)
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p2)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }
}
