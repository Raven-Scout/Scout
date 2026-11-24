import Combine
import SwiftUI

// MARK: - Pure rules

/// Somewhere a change's reason can take the owner.
nonisolated enum DetailLink: Equatable, Sendable {
    /// A note in the Knowledge Base tab, by vault-relative path.
    case note(label: String, path: String)
    /// A file inside the vault, opened with the system.
    case file(label: String, url: URL)

    var label: String {
        switch self {
        case .note(let label, _), .file(let label, _): return label
        }
    }

    /// Opens the link through the two handlers the caller supplies.
    @MainActor
    func perform(openNote: (String) -> Void, openFile: (URL) -> Void) {
        switch self {
        case .note(_, let path): openNote(path)
        case .file(_, let url): openFile(url)
        }
    }
}

/// One before/after pair in the detail's small multiples.
nonisolated struct DetailMultiple: Equatable, Sendable {
    let title: String
    let before: Double?
    let after: Double?
}

/// The numbers and words behind the change list and the change detail. Pure.
nonisolated enum DetailModel {
    static let reasonNotRecorded = "Reason not recorded"
    static let guessedSuffix = " (guessed from the commit)"
    static let mistakeAuditPath = "knowledge-base/scout-mistake-audit.md"
    static let undoQueuedMessage = "Undo queued: the next dreaming run takes back the lines this change made."
    static let wrongRecordedMessage = "Noted: this verdict is marked wrong."
    static let invalidMessage = "Scout can't do that for this change."
    static let daysEachSide = 14

    // MARK: Sparkline

    /// The engagement (feedback plus reliance) of the 14 recorded days before the change and the 14 from
    /// its day on, oldest first. A day the engine didn't record isn't invented. The change's own day counts
    /// as "after".
    static func sparkline(days: [EngagementDay], around ts: Date, calendar: Calendar) -> (before: [Int], after: [Int]) {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        let changeDay = calendar.startOfDay(for: ts)
        guard let start = calendar.date(byAdding: .day, value: -daysEachSide, to: changeDay),
              let end = calendar.date(byAdding: .day, value: daysEachSide, to: changeDay) else { return ([], []) }
        let dated: [(Date, Int)] = days.compactMap { day in
            formatter.date(from: day.date).map { ($0, day.feedback + day.reliance) }
        }.sorted { $0.0 < $1.0 }
        let before = dated.filter { $0.0 >= start && $0.0 < changeDay }.map(\.1)
        let after = dated.filter { $0.0 >= changeDay && $0.0 < end }.map(\.1)
        return (before, after)
    }

    // MARK: Words

    /// The reason in plain words. Never an identifier: an empty gloss reads "Reason not recorded".
    static func gloss(_ why: ChangeWhy) -> String {
        let text = why.gloss.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return reasonNotRecorded }
        return why.inferred ? text + guessedSuffix : text
    }

    /// What `auto_rollback` would have done to this change under each mode, only while calibrating.
    static func wouldHaveDoneLines(_ test: ChangeTest) -> [String] {
        guard test.calibrating else { return [] }
        let modes: [(key: String, label: String)] = [
            ("targeted", "Targeted"), ("one_at_a_time", "One-at-a-time"),
        ]
        return modes.compactMap { mode in
            guard let would = test.wouldHaveDone[mode.key] else { return nil }
            return "\(mode.label) would have \(would ? "rolled this back" : "left this alone")."
        }
    }

    static func overlapLine(_ test: ChangeTest) -> String? {
        let count = test.overlap.count
        guard count > 0 else { return nil }
        return "\(count) \(count == 1 ? "change" : "changes") landed in this window; the effect is shared"
    }

    /// Where the change's undo stands, in words. `pending` is an Undo the owner asked for that no refreshed
    /// row shows yet.
    static func rollbackStatus(_ row: ChangeRow, in changes: [ChangeRow], pending: Bool) -> String? {
        if let by = row.test.rolledBackBy {
            let name = changes.first { $0.id == by }?.summary
            return name.map { "Rolled back by \u{201C}\($0)\u{201D}." } ?? "Rolled back by a later change."
        }
        if pending { return undoQueuedMessage }
        if let reverts = row.reverts {
            let name = changes.first { $0.id == reverts }?.summary
            return name.map { "This change takes back \u{201C}\($0)\u{201D}." } ?? "This change takes back an earlier one."
        }
        return nil
    }

    /// Why the verdict is what it is: each reason as a plain phrase. Anything shaped like an id is left out.
    static func reasonWords(_ reasons: [String]) -> [String] {
        reasons.filter { !$0.contains("chg-") && !$0.isEmpty }.map { reason in
            let words = reason.replacingOccurrences(of: "-", with: " ").replacingOccurrences(of: "_", with: " ")
            return words.prefix(1).uppercased() + words.dropFirst()
        }
    }

    // MARK: Small multiples

    /// Engagement per day, noise per day, then each target's recurrences, baseline against after.
    /// Empty until the engine has measured a baseline.
    static func multiples(_ row: ChangeRow) -> [DetailMultiple] {
        guard let baseline = row.test.baseline else { return [] }
        let after = row.test.after
        var rows = [
            DetailMultiple(title: "Your engagement per day", before: baseline.engagement, after: after?.engagement),
            DetailMultiple(title: "Noise per day", before: baseline.noise, after: after?.noise),
        ]
        var unnamed = 0
        for key in baseline.recurrences.keys.sorted() {
            let title: String
            if key == row.why.ref, !row.why.gloss.trimmingCharacters(in: .whitespaces).isEmpty {
                title = "Came back: \(row.why.gloss)"
            } else {
                unnamed += 1
                title = "Came back: a related mistake (\(unnamed))"
            }
            rows.append(DetailMultiple(title: title, before: Double(baseline.recurrences[key] ?? 0),
                                       after: after.map { Double($0.recurrences[key] ?? 0) }))
        }
        return rows
    }

    // MARK: Links

    /// `^[a-z0-9][a-z0-9-]{0,80}$`, checked by hand: a regex `$` also matches before a trailing newline.
    static func isWishlistSlug(_ s: String) -> Bool {
        let bytes = Array(s.utf8)
        guard (1...81).contains(bytes.count) else { return false }
        func alnum(_ b: UInt8) -> Bool { (b >= 48 && b <= 57) || (b >= 97 && b <= 122) }
        return alnum(bytes[0]) && bytes.dropFirst().allSatisfy { alnum($0) || $0 == UInt8(ascii: "-") }
    }

    /// `docs/wishlist/<slug>.md` under the vault, when the slug is well formed and the file is really
    /// inside the vault's wishlist folder once symlinks are followed. The slug is engine input.
    static func wishlistURL(slug: String, vault: URL?, exists: (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) }) -> URL? {
        guard isWishlistSlug(slug), let vault else { return nil }
        let root = vault.resolvingSymlinksInPath().standardizedFileURL
        let file = root.appendingPathComponent("docs/wishlist/\(slug).md").resolvingSymlinksInPath().standardizedFileURL
        guard file.path.hasPrefix(root.path + "/docs/wishlist/"), exists(file) else { return nil }
        return file
    }

    /// What the reason links to: a pattern opens the mistake audit; a wishlist reason opens its file.
    static func links(for why: ChangeWhy, vault: URL?, exists: (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) }) -> [DetailLink] {
        switch why.type {
        case "pattern":
            return [.note(label: "Open the mistake audit", path: mistakeAuditPath)]
        case "wishlist":
            return wishlistURL(slug: why.ref, vault: vault, exists: exists)
                .map { [.file(label: "Open the wishlist entry", url: $0)] } ?? []
        default:
            return []
        }
    }

    // MARK: Pending undos

    /// The undo ids still waiting: those whose row has not been shown rolled back by a refresh.
    static func stillPending(_ pending: Set<String>, rows: [ChangeRow]) -> Set<String> {
        pending.filter { id in
            guard let row = rows.first(where: { $0.id == id }) else { return false }
            return row.test.rolledBackBy == nil
        }
    }
}

// MARK: - State

/// What the open change's detail is doing: the diff it loaded, the Undo it is waiting to confirm, and
/// the undos that were queued. Shared by the list and its sheet, so a queued undo survives closing the sheet.
@MainActor
final class ChangeDetailModel: ObservableObject {
    enum DiffState: Equatable {
        case idle
        case loading
        /// The diff as the engine printed it. Shown on screen only; never logged.
        case loaded(String)
        case failed(String)
    }

    /// The change the owner is being asked to undo.
    @Published var confirmingUndoID: String?
    @Published private(set) var message: String?
    @Published private(set) var diff: DiffState = .idle
    @Published private(set) var busy = false
    private var openID: String?

    /// Starts a fresh detail: nothing from the last change carries over.
    func open(_ id: String) {
        guard openID != id else { return }
        openID = id
        message = nil
        diff = .idle
        confirmingUndoID = nil
    }

    /// True while a result for `id` should still be shown: nothing else has been opened since.
    private func isCurrent(_ id: String) -> Bool { openID == nil || openID == id }

    /// `pending` is `ChangesDecisions.pendingUndos`, which outlives this model and the tab.
    func canUndo(_ row: ChangeRow, verified: Bool, pending: Set<String>) -> Bool {
        ChangeActions.canUndo(row, verified: verified) && !pending.contains(row.id) && !busy
    }

    func canMarkWrong(_ row: ChangeRow) -> Bool {
        ChangeActions.canMarkWrong(row) && !busy
    }

    /// Opens the confirmation. Nothing runs until `confirmUndo`.
    func askToUndo(_ row: ChangeRow, verified: Bool, pending: Set<String>) {
        guard canUndo(row, verified: verified, pending: pending) else { return }
        confirmingUndoID = row.id
    }

    func cancelUndo() { confirmingUndoID = nil }

    /// The owner confirmed. Undo is queued, not done: the engine records the decision and a later dreaming
    /// run applies it or declines it.
    /// The owner pressed Undo in the confirmation: takes the id they were asked about (nil when nothing
    /// was asked) and closes the question. Synchronous, because the dialog's own dismissal clears it.
    func confirmUndo() -> String? {
        defer { confirmingUndoID = nil }
        return confirmingUndoID
    }

    /// Runs the undo for an id `confirmUndo()` returned.
    func performUndo(id: String, decisions: ChangesDecisions, service: ChangesService) async {
        guard let snapshot = service.snapshot, let row = snapshot.changes.changes.first(where: { $0.id == id }),
              canUndo(row, verified: snapshot.changes.verified, pending: decisions.pendingUndos) else { return }
        busy = true
        defer { busy = false }
        switch await decisions.perform(.undo(changeID: id)) {
        case .done:
            decisions.noteUndoQueued(id)
            if isCurrent(id) { message = DetailModel.undoQueuedMessage }
            await service.refresh()
            decisions.reconcilePendingUndos(with: service)
        case .refused(let words):
            if isCurrent(id) { message = words }
        case .invalid:
            if isCurrent(id) { message = DetailModel.invalidMessage }
        }
    }

    func markWrong(_ row: ChangeRow, decisions: ChangesDecisions, service: ChangesService) async {
        guard canMarkWrong(row) else { return }
        busy = true
        defer { busy = false }
        switch await decisions.perform(.wrong(changeID: row.id)) {
        case .done:
            if isCurrent(row.id) { message = DetailModel.wrongRecordedMessage }
            await service.refresh()
            decisions.reconcilePendingUndos(with: service)
        case .refused(let words):
            if isCurrent(row.id) { message = words }
        case .invalid:
            if isCurrent(row.id) { message = DetailModel.invalidMessage }
        }
    }

    /// Loads the diff when the owner asks for it.
    func loadDiff(for row: ChangeRow, decisions: ChangesDecisions) async {
        guard diff != .loading else { return }
        diff = .loading
        let result = await decisions.perform(.diff(changeID: row.id))
        // The owner may have opened another change while the engine ran: that result is not theirs to see.
        guard isCurrent(row.id) else { return }
        switch result {
        case .done(let text):
            diff = .loaded(text)
        case .refused(let words):
            diff = .failed(words)
        case .invalid:
            diff = .failed(DetailModel.invalidMessage)
        }
    }
}

// MARK: - View

/// One change in full: what it did, the before/after numbers, where its undo stands, its diff on
/// demand, and the two owner decisions.
struct ChangeDetailView: View {
    let changeID: String
    var vaultDirectory: URL?
    @ObservedObject var model: ChangeDetailModel
    @ObservedObject var decisions: ChangesDecisions
    @ObservedObject var service: ChangesService
    let openNote: (String) -> Void
    let openFile: (URL) -> Void
    let onClose: () -> Void

    static let undoQuestion = "Undo this change?"
    static let undoDetail = "The next dreaming run takes back the lines it made."
    static let missingMessage = "This change is no longer in the list."

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Spacer()
                Button("Close", action: onClose)
                    .buttonStyle(.ds(.secondary, size: .small))
                    .keyboardShortcut(.cancelAction)
            }
            .padding(.bottom, 8)
            ScrollView {
                if let snapshot = service.snapshot, let row = snapshot.changes.changes.first(where: { $0.id == changeID }) {
                    detail(row, document: snapshot.changes)
                } else {
                    Text(Self.missingMessage)
                        .font(DS.font(.body))
                        .foregroundStyle(DS.Ink.p3)
                }
            }
        }
        .padding(24)
        .frame(minWidth: 560, idealWidth: 640, minHeight: 520, idealHeight: 680)
        .background(DS.Paper.base)
        .onAppear { model.open(changeID) }
        .onChange(of: service.state) { _, _ in decisions.reconcilePendingUndos(with: service) }
    }

    @ViewBuilder
    private func detail(_ row: ChangeRow, document: ChangesDocument) -> some View {
        let pending = decisions.pendingUndos.contains(row.id) && row.test.rolledBackBy == nil
        VStack(alignment: .leading, spacing: 16) {
            header(row)
            if let status = DetailModel.rollbackStatus(row, in: document.changes, pending: pending) {
                Text(status)
                    .font(DS.font(.body, weight: .semibold))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
            }
            multiples(row)
            if let overlap = DetailModel.overlapLine(row.test) {
                Text(overlap).font(DS.font(.label)).foregroundStyle(DS.Ink.p2)
            }
            ForEach(DetailModel.wouldHaveDoneLines(row.test), id: \.self) { line in
                Text(line).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
            }
            reasons(row)
            links(row)
            diffSection(row)
            actions(row, verified: document.verified)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func header(_ row: ChangeRow) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(verbatim: row.summary)
                    .dsFont(.headline, .serif, weight: .medium)
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                VerdictPill(test: row.test, inferred: row.why.inferred)
            }
            Text(verbatim: DetailModel.gloss(row.why))
                .font(DS.font(.body))
                .foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            Text(row.ts.formatted(date: .abbreviated, time: .shortened))
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
        }
    }

    @ViewBuilder
    private func multiples(_ row: ChangeRow) -> some View {
        let rows = DetailModel.multiples(row)
        if rows.isEmpty {
            Text("Not enough days measured yet to compare before and after.")
                .font(DS.font(.label))
                .foregroundStyle(DS.Ink.p3)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                Text("Before and after")
                    .dsFont(.body, .serif, weight: .semibold)
                    .foregroundStyle(DS.Ink.p1)
                let top = max(rows.flatMap { [$0.before ?? 0, $0.after ?? 0] }.max() ?? 0, 0.0001)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, item in
                    multipleRow(item, scaleMax: top)
                }
            }
        }
    }

    private func multipleRow(_ item: DetailMultiple, scaleMax: Double) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(verbatim: item.title).font(DS.font(.label)).foregroundStyle(DS.Ink.p2)
            bar("Before", value: item.before, scaleMax: scaleMax, color: DS.Ink.p4)
            bar("After", value: item.after, scaleMax: scaleMax, color: DS.Ink.p2)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(item.title): before \(Self.number(item.before)), after \(Self.number(item.after))")
    }

    private func bar(_ name: String, value: Double?, scaleMax: Double, color: Color) -> some View {
        HStack(spacing: 8) {
            Text(name).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3).frame(width: 44, alignment: .leading)
            GeometryReader { geo in
                RoundedRectangle(cornerRadius: 2).fill(color)
                    .frame(width: max(1, geo.size.width * CGFloat((value ?? 0) / scaleMax)), height: 8)
                    .frame(maxHeight: .infinity, alignment: .center)
            }
            .frame(height: 10)
            Text(Self.number(value)).font(DS.font(.caption)).foregroundStyle(DS.Ink.p2)
                .frame(width: 44, alignment: .trailing)
        }
        .accessibilityHidden(true)
    }

    static func number(_ value: Double?) -> String {
        guard let value else { return "not measured" }
        return value.formatted(.number.precision(.fractionLength(0...2)))
    }

    @ViewBuilder
    private func reasons(_ row: ChangeRow) -> some View {
        let words = DetailModel.reasonWords(row.test.reasons)
        if !words.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                Text("Why this verdict").font(DS.font(.label, weight: .semibold)).foregroundStyle(DS.Ink.p2)
                ForEach(words, id: \.self) { Text($0).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3) }
            }
        }
    }

    @ViewBuilder
    private func links(_ row: ChangeRow) -> some View {
        let links = DetailModel.links(for: row.why, vault: vaultDirectory)
        if !links.isEmpty {
            HStack(spacing: 10) {
                ForEach(links, id: \.label) { link in
                    Button(link.label) { link.perform(openNote: openNote, openFile: openFile) }
                        .buttonStyle(.ds(.secondary, size: .small))
                }
            }
        }
    }

    @ViewBuilder
    private func diffSection(_ row: ChangeRow) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            switch model.diff {
            case .idle:
                Button("Show the diff") { Task { await model.loadDiff(for: row, decisions: decisions) } }
                    .buttonStyle(.ds(.secondary, size: .small))
            case .loading:
                ProgressView().controlSize(.small)
            case .loaded(let text):
                ScrollView([.horizontal, .vertical]) {
                    Text(verbatim: text.isEmpty ? "This change has no recorded diff." : text)
                        .dsFont(.caption, .mono)
                        .foregroundStyle(DS.Ink.p1)
                        .textSelection(.enabled)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 260)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.raised))
                .accessibilityLabel("Diff of this change")
            case .failed(let words):
                Text(words).font(DS.font(.label)).foregroundStyle(DS.Status.err)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func actions(_ row: ChangeRow, verified: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Button("Undo") { model.askToUndo(row, verified: verified, pending: decisions.pendingUndos) }
                    .buttonStyle(.ds(.secondary, size: .small))
                    .disabled(!model.canUndo(row, verified: verified, pending: decisions.pendingUndos))
                Button("This verdict is wrong") {
                    Task { await model.markWrong(row, decisions: decisions, service: service) }
                }
                .buttonStyle(.ds(.secondary, size: .small))
                .disabled(!model.canMarkWrong(row))
            }
            if let message = model.message {
                Text(message)
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .confirmationDialog(Self.undoQuestion, isPresented: Binding(
            get: { model.confirmingUndoID != nil }, set: { if !$0 { model.cancelUndo() } }
        ), titleVisibility: .visible) {
            Button("Undo", role: .destructive) {
                if let id = model.confirmUndo() {
                    Task { await model.performUndo(id: id, decisions: decisions, service: service) }
                }
            }
            Button("Cancel", role: .cancel) { model.cancelUndo() }
        } message: {
            Text(Self.undoDetail)
        }
    }
}
