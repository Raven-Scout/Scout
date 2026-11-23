import SwiftUI
import AppKit

/// A task in the Action Items List. Top-level tasks render as a collapsible
/// card — a scannable header (priority stripe · prefix · title · source chips ·
/// quick actions) with the dense detail (body, comments, full actions, comment
/// composer) tucked behind a chevron. Urgent tasks start expanded; everything
/// else starts collapsed. Nested sub-tasks render as light indented rows.
///
/// Inspired by the triage artifact's card + progressive-disclosure layout,
/// keeping Scout's editorial palette.
struct TaskCardView: View {
    let task: ActionTask
    let kind: ActionSection.Kind
    let displayedDate: Date
    let scoutDirectory: URL
    let selection: Binding<Set<UUID>>?
    // `@MainActor` is load-bearing: with default-MainActor + approachable
    // concurrency, a non-isolated async closure type would carry the WriteOp
    // across an isolation boundary as a `sending` value, and the reabstraction
    // thunk over-releases its String payloads → EXC_BAD_ACCESS reading the op
    // in the writer. Keeping the closure MainActor-isolated avoids that hop.
    let onOp: @MainActor (WriteOp, Int?) async throws -> Void
    /// Called when the user collapses the card. Recently Completed uses it to
    /// turn an opened done task back into its one-line row.
    let onCollapse: (() -> Void)?
    /// Display options from the profile (#52). Defaults draw today's card.
    let density: ActionItemsDisplay.Density
    let fields: ActionItemsDisplay.Fields
    /// Set when the caller decides the starting state (an opened done row);
    /// a density change then leaves the card alone.
    private let startsExpandedOverride: Bool?
    /// Where the card's unsent comments are kept while it is collapsed.
    private let drafts: CommentDraftStore

    @State private var inlineError: String?
    @State private var expanded: Bool
    @State private var showingQuickSnooze = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.openKBNote) private var openKBNote
    @Environment(\.revealTaggedTask) private var revealTaggedTask
    /// Off while the engine check fails: the page's banner says so.
    @Environment(\.actionItemsWritesEnabled) private var writesEnabled
    /// The List's keyboard model: which card is current, and its ⌘↵ (#50).
    @Environment(\.actionItemsKeyboard) private var keyboard

    init(
        task: ActionTask,
        kind: ActionSection.Kind,
        displayedDate: Date,
        scoutDirectory: URL,
        selection: Binding<Set<UUID>>? = nil,
        startsExpanded: Bool? = nil,
        onCollapse: (() -> Void)? = nil,
        density: ActionItemsDisplay.Density = .comfortable,
        fields: ActionItemsDisplay.Fields = .init(),
        drafts: CommentDraftStore = .shared,
        onOp: @escaping @MainActor (WriteOp, Int?) async throws -> Void
    ) {
        self.task = task
        self.kind = kind
        self.displayedDate = displayedDate
        self.scoutDirectory = scoutDirectory
        self.selection = selection
        self.onOp = onOp
        self.onCollapse = onCollapse
        self.density = density
        self.fields = fields
        self.startsExpandedOverride = startsExpanded
        self.drafts = drafts
        _expanded = State(initialValue: startsExpanded
            ?? Self.startsExpanded(kind: task.snoozedFromKind ?? kind, density: density))
    }

    /// Urgent opens by default, since its detail is what you want right away.
    /// Compact density starts every card collapsed.
    static func startsExpanded(kind: ActionSection.Kind, density: ActionItemsDisplay.Density) -> Bool {
        kind == .urgent && density == .comfortable
    }

    /// The chips the profile lets the collapsed card show. With `refs` off only
    /// the "carried" chip stays: it is the task's age, not a reference.
    static func visibleChips(_ chips: [TaskChip], fields: ActionItemsDisplay.Fields) -> [TaskChip] {
        fields.refs ? chips : chips.filter { $0.glyph == .carry }
    }

    var body: some View {
        if isNested {
            nestedRow
        } else {
            card
        }
    }

    /// Sub-tasks (depth ≥ 1) stay lightweight — no card chrome, no collapse,
    /// indented under their parent so the hierarchy reads at a glance.
    private var isNested: Bool { task.indentLevel > 0 }

    /// Kind used for visual treatment. Honors the source-section hint recorded
    /// by `scoutctl snooze --from-kind` so an urgent task that carries forward
    /// into the `🛌 Snoozed` section stays visually urgent.
    var effectiveKind: ActionSection.Kind { task.snoozedFromKind ?? kind }

    // MARK: - Card

    private var card: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if expanded {
                detail
                    .padding(.horizontal, 14)
                    .padding(.bottom, 14)
            } else if let err = Self.collapsedError(expanded: expanded, inlineError: inlineError) {
                inlineErrorLabel(err)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 10)
            }
        }
        // Picked for copying is `marked`; keyboard-current is `selected`.
        .editorialCard(padding: 0, cornerRadius: 10, selected: isCursor, marked: isSelected,
                       accentBar: DS.priorityColor(effectiveKind).opacity(task.done ? 0.5 : 1))
        // While the list itself has the keys, the current card rings (T4).
        .dsCursorRing(keyboard.ringID == task.id, cornerRadius: 10)
        // A click anywhere on the card makes it keyboard-current and gives the
        // list the keys, alongside whatever the click hit (a button still does
        // its own thing; a comment editor keeps the keys it took).
        .simultaneousGesture(TapGesture().onEnded { keyboard.focusCard?(task.id) })
        .contextMenu { contextMenuItems }
        .padding(.bottom, 10)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: isSelected)
        .onChange(of: keyboard.toggleRequest) { _, request in
            if request?.id == task.id { toggle() }
        }
        // Picking a density applies to the cards already on screen too, not
        // only to cards created after the change.
        .onChange(of: density) { _, newDensity in
            guard startsExpandedOverride == nil else { return }
            expanded = Self.startsExpanded(kind: effectiveKind, density: newDensity)
        }
    }

    // MARK: - Header (always visible, scannable)

    private var header: some View {
        // Computed once per body pass — `chips` walks the task's deep links and
        // formats the carried-from date, so gating and rendering off two
        // separate evaluations doubles that work for every card.
        let chips = self.chips
        let summary = task.summary
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if selection != nil {
                    selectionButton
                }
                // Not selectable: a click on the title opens or closes the card.
                InlineMarkdownText(task.subject)
                    .dsFont(.headline, .serif, weight: .medium)
                    .foregroundStyle(task.done ? DS.Ink.p3 : DS.Ink.p1)
                    .strikethrough(task.done, color: DS.Ink.decorative)
                    .lineLimit(expanded ? nil : 2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture { toggle() }
                    // On the title alone: on the card it reached every
                    // button inside ("Mark done, selected").
                    .accessibilityAddTraits(isCursor ? .isSelected : [])
                if !expanded { quickActions }
                if Self.showsDraftMarker(expanded: expanded, hasDraft: drafts.mark(for: draftTaskKey).isDrafted) {
                    draftMarker
                }
                trailingStatus
                chevron
            }
            if !expanded && !summary.isEmpty && density == .comfortable {
                InlineMarkdownText(summary)
                    .dsFont(.body, .serif)
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture { toggle() }
            }
            if !chips.isEmpty {
                chipRow(chips)
            }
        }
        .padding(density == .compact ? 8 : 14)
        .contentShape(Rectangle())
    }

    /// The kept comment draft's marker (`CommentDraftStore`): on a collapsed
    /// card only, where the draft itself isn't on screen.
    static func showsDraftMarker(expanded: Bool, hasDraft: Bool) -> Bool {
        !expanded && hasDraft
    }

    private var draftTaskKey: CommentDraftTaskKey {
        CommentDraftTaskKey(task: task, displayedDate: displayedDate)
    }

    private var draftMarker: some View {
        DSPill("Draft", tone: .accent, symbol: "square.and.pencil")
            .help("An unsent comment is kept here: open the card to send or cancel it")
            .accessibilityLabel("Unsent comment draft")
    }

    private var chevron: some View {
        Button { toggle() } label: {
            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                .font(DS.font(.caption, weight: .medium))
                .foregroundStyle(DS.Ink.p4)
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        .help(expanded ? "Collapse action item" : "Expand action item")
        .accessibilityLabel("Action item details")
        .accessibilityValue(expanded ? "Expanded" : "Collapsed")
    }

    private func toggle() {
        withMotion(.easeInOut(duration: 0.15)) { expanded.toggle() }
        if !expanded { onCollapse?() }
    }

    /// Whether the comment composer shows. Open tasks always; a done task only
    /// with a `[#TAG]`, because scoutctl reaches a done task by `--by-id` alone
    /// (its `--subject` lookup matches open tasks).
    static func canComment(_ task: ActionTask) -> Bool {
        !task.done || task.shortPrefix != nil
    }

    /// Whether a comment's Edit and Delete show: only where a comment could be
    /// added, and only while writes are on. With the engine check failing
    /// they'd fail every time, so they're left out like any write.
    static func canChangeComments(_ task: ActionTask, writesEnabled: Bool) -> Bool {
        writesEnabled && canComment(task)
    }

    // MARK: - Source/context chips

    private var chips: [TaskChip] {
        var chips = Self.visibleChips(
            TaskChip.chips(for: task, carriedLabel: task.carriedInFrom.map { dateShort($0) }),
            fields: fields
        )
        if fields.comments && !task.comments.isEmpty {
            chips.append(TaskChip(glyph: .comments, label: "\(task.comments.count)"))
        }
        return chips
    }

    private func chipRow(_ chips: [TaskChip]) -> some View {
        HStack(spacing: 6) {
            ForEach(chips) { chip in
                chipView(for: chip)
            }
        }
    }

    /// Renders a chip per its targets: an in-app action when the chip has one
    /// and this surface handles it, else static text (0 links), a button that
    /// opens directly (1 link), or a dropdown listing each target (>1 links).
    @ViewBuilder
    private func chipView(for chip: TaskChip) -> some View {
        if let inApp = inAppAction(for: chip) {
            Button(action: inApp.action) {
                chipBody(for: chip)
            }
            .buttonStyle(.plainHit)
            .help(inApp.help)
            // The system pointer style, not NSCursor push/pop: opening a note
            // switches tabs under the pointer, so a hover-exit never comes.
            .pointerStyle(.link)
        } else {
            linkChipView(for: chip)
        }
    }

    private func inAppAction(for chip: TaskChip) -> (action: () -> Void, help: String)? {
        switch chip.inApp {
        case .note(let target)?:
            guard let openKBNote else { return nil }
            return ({ openKBNote(target) }, "Open \(chip.label) in the Knowledge Base")
        case .task(let tag)?:
            guard let revealTaggedTask else { return nil }
            return ({ revealTaggedTask(tag) }, "Show #\(tag) in the list")
        case nil:
            return nil
        }
    }

    @ViewBuilder
    private func linkChipView(for chip: TaskChip) -> some View {
        switch chip.links.count {
        case 0:
            chipBody(for: chip)
        case 1:
            Button {
                NSWorkspace.shared.open(chip.links[0].url)
            } label: {
                chipBody(for: chip)
            }
            .buttonStyle(.plainHit)
            .help(chip.links[0].url.absoluteString)
            .keyWindowPointingHand()
        default:
            Menu {
                ForEach(chip.links) { link in
                    Button(link.label) { NSWorkspace.shared.open(link.url) }
                }
            } label: {
                chipBody(for: chip)
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .keyWindowPointingHand()
        }
    }

    private func chipBody(for chip: TaskChip) -> some View {
        HStack(spacing: 4) {
            Image(systemName: chipGlyph(chip.glyph))
                .font(DS.font(.micro))
            Text(chip.label)
        }
        .dsTag(.mono)
    }

    private func chipGlyph(_ glyph: TaskChip.Glyph) -> String {
        switch glyph {
        case .github:   return "arrow.triangle.pull"
        case .linear:   return "circle.grid.2x2"
        case .slack:    return "bubble.left.and.bubble.right"
        case .carry:    return "calendar"
        case .entity:   return "doc.text"
        case .crossRef: return "number.square"
        case .comments: return "text.bubble"
        case .plain:    return "tag"
        }
    }

    // MARK: - Quick actions (collapsed only)

    private var quickActions: some View {
        HStack(spacing: 4) {
            if task.done {
                iconButton("arrow.uturn.backward", help: "Reopen") {
                    Task { await runOp(.reopen(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                }
            } else {
                iconButton("checkmark", help: "Mark done", shortcut: doneShortcut) {
                    Task { await runOp(.markDone(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
                }
                iconButton("moon.zzz", help: "Snooze") { showingQuickSnooze = true }
                    .popover(isPresented: $showingQuickSnooze) {
                        SnoozePopoverView(sourceDate: displayedDate) { target in
                            await runOp(.snooze(
                                subject: task.matchableSubject,
                                shortPrefix: task.shortPrefix,
                                until: target,
                                fromKind: kind.rawValue
                            ))
                            showingQuickSnooze = false
                        } onCancel: {
                            showingQuickSnooze = false
                        }
                    }
            }
        }
        // Every quick action saves; the page's banner says why they're off.
        .disabled(!writesEnabled)
        .opacity(writesEnabled ? 1 : 0.45)
    }

    private func iconButton(_ systemImage: String, help: String, shortcut: KeyboardShortcut? = nil,
                            action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(DS.font(.caption))
                .foregroundStyle(DS.Ink.p3)
                .frame(width: 24, height: 22)
                .background(RoundedRectangle(cornerRadius: 5).fill(DS.Paper.base))
                .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        .keyboardShortcut(shortcut)
        .help(ShortcutLabel.help(help, shortcut))
        .accessibilityLabel(help)
    }

    @ViewBuilder
    private var trailingStatus: some View {
        switch Self.trailingStatus(task, fields: fields) {
        case .done(let label):
            DSPill(label, tone: .ok)
        case .snoozed(let until):
            HStack(spacing: 3) {
                Image(systemName: "moon.zzz.fill").imageScale(.small)
                Text(dateShort(until))
            }
            .dsTag(.mono)
        case .plan(let label):
            HStack(spacing: 3) {
                Image(systemName: "clock").imageScale(.small)
                Text(label)
            }
            .dsTag(.mono)
            .help(Self.planChipHelp(task.plan))
        case .none:
            EmptyView()
        }
    }

    enum TrailingStatus: Equatable {
        case done(String)
        case snoozed(Date)
        case plan(String)
        case none
    }

    /// What the header's trailing slot shows: "Done" ("Done · 1h" once
    /// `/scout-plan` has the actual time), the snooze date, or the plan chip.
    /// A snoozed task never shows the plan chip, also when the profile hides
    /// its snooze date.
    static func trailingStatus(_ task: ActionTask, fields: ActionItemsDisplay.Fields, today: Date = Date()) -> TrailingStatus {
        if task.done {
            guard fields.plan, let actual = task.plan.actualMinutes else { return .done("Done") }
            return .done("Done · \(TaskPlan.formatDuration(actual))")
        }
        if let until = task.snoozedUntil {
            return fields.snooze ? .snoozed(until) : .none
        }
        if fields.plan, let label = planChipLabel(task.plan, today: today) {
            return .plan(label)
        }
        return .none
    }

    /// The `/scout-plan` chip: "10:00-10:45 · 45m", the weekday first when the
    /// block is not today, "· took 1h" once the actual is in, "45m" for an
    /// estimate without a block. Nil without a plan.
    static func planChipLabel(_ plan: TaskPlan, today: Date = Date()) -> String? {
        var parts: [String] = []
        if let block = plan.block {
            var range = "\(block.start)-\(block.end)"
            if block.date != isoDay.string(from: today), let day = isoDay.date(from: block.date) {
                range = "\(weekday.string(from: day)) " + range
            }
            parts.append(range)
        }
        if let estimate = plan.estimateMinutes {
            parts.append(TaskPlan.formatDuration(estimate))
        }
        if let actual = plan.actualMinutes {
            parts.append("took \(TaskPlan.formatDuration(actual))")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// Tooltip: "Planned 2026-09-30 10:00-10:45, estimate 45m (raw 30m), deep, took 1h on 2026-09-30".
    static func planChipHelp(_ plan: TaskPlan) -> String {
        var parts: [String] = []
        if let block = plan.block {
            parts.append("planned \(block.date) \(block.start)-\(block.end)")
        }
        if let estimate = plan.estimateMinutes {
            var line = "estimate \(TaskPlan.formatDuration(estimate))"
            if let raw = plan.rawMinutes, raw != estimate {
                line += " (raw \(TaskPlan.formatDuration(raw)))"
            }
            if let kind = plan.kind { line += ", \(kind)" }
            parts.append(line)
        }
        if let actual = plan.actualMinutes {
            var line = "took \(TaskPlan.formatDuration(actual))"
            if let day = plan.actualDate { line += " on \(day)" }
            parts.append(line)
        }
        let text = parts.joined(separator: ", ")
        return text.prefix(1).uppercased() + text.dropFirst()
    }

    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private static let weekday: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEE"
        return f
    }()

    // MARK: - Expanded detail

    // Typed properties rather than inline `cond ? nil : { … }` ternaries:
    // Xcode 26.3 (CI) can't type-check those inside the detail VStack.
    private var editCommentHandler: ((Int, String) async -> Bool)? {
        guard Self.canChangeComments(task, writesEnabled: writesEnabled) else { return nil }
        return { index, newText in
            // The row hides Edit for a comment without a selector; this is
            // the backstop, so a guessed index is never sent.
            guard let selector = task.commentSelector(at: index) else { return false }
            return await runOp(.editComment(
                subject: task.matchableSubject,
                shortPrefix: task.shortPrefix,
                selector: selector,
                newText: newText
            ))
        }
    }

    private var deleteCommentHandler: ((Int) async -> Void)? {
        guard Self.canChangeComments(task, writesEnabled: writesEnabled) else { return nil }
        return { index in
            guard let selector = task.commentSelector(at: index) else { return }
            await runOp(.deleteComment(
                subject: task.matchableSubject,
                shortPrefix: task.shortPrefix,
                selector: selector
            ))
        }
    }

    private var detail: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !task.body.isEmpty {
                TaskBodyView(rawBody: task.body)
            }

            if !task.details.isEmpty {
                TaskDetailsView(details: task.details)
            }

            // Keyed by tag so a re-tagged item drops the old note's cached text.
            TaskContextNoteSection(tag: task.shortPrefix, scoutDirectory: scoutDirectory)
                .id(task.shortPrefix)

            if !task.comments.isEmpty {
                // Editing and deleting reach the task the same way adding does,
                // so they follow the same rule.
                CommentListView(
                    comments: task.comments,
                    onEdit: editCommentHandler,
                    onDelete: deleteCommentHandler,
                    draftTask: draftTaskKey,
                    drafts: drafts,
                    task: task
                )
            }

            if !task.deepLinks.isEmpty {
                // The header's ref chips are these links' Tab stops while they show.
                TaskLinksView(links: task.deepLinks, headerHasChips: fields.refs)
            }

            TaskActionsView(
                task: task,
                kind: effectiveKind,
                displayedDate: displayedDate,
                scoutDirectory: scoutDirectory,
                doneShortcut: doneShortcut
            ) { op in
                await runOp(op)
            }

            if Self.canComment(task) {
                CommentComposerView(task: task, displayedDate: displayedDate, drafts: drafts) { text in
                    let author = CommentAuthor.configuredName()
                    return await runOp(.addComment(
                        subject: task.matchableSubject,
                        shortPrefix: task.shortPrefix,
                        text: text,
                        author: author
                    ))
                }
                .disabled(!writesEnabled)
            }

            if let err = inlineError {
                inlineErrorLabel(err)
            }
        }
    }

    // MARK: - Nested sub-task row

    private var nestedRow: some View {
        HStack(alignment: .top, spacing: 10) {
            if selection != nil {
                selectionButton
            }
            Circle()
                .fill(DS.priorityColor(effectiveKind))
                .frame(width: 5, height: 5)
                .opacity(task.done ? 0.5 : 0.8)
                .padding(.top, 7)
            VStack(alignment: .leading, spacing: 4) {
                InlineMarkdownText(task.subject)
                    .dsFont(.body, .serif)
                    .foregroundStyle(task.done ? DS.Ink.p3 : DS.Ink.p2)
                    .strikethrough(task.done, color: DS.Ink.decorative)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !task.summary.isEmpty {
                    InlineMarkdownText(task.summary)
                        .dsFont(.label, .serif)
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .textSelection(.enabled)
        }
        .padding(.vertical, 6)
        .padding(.leading, CGFloat(task.indentLevel) * 24 + 16)
        .padding(.trailing, 14)
    }

    // MARK: - Keyboard and context menu (#50)

    private var isCursor: Bool { keyboard.cursorID == task.id }

    /// ⌘↵ marks this card done while it is the keyboard-current one. Only one
    /// card binds it, so the key always means one card.
    private var doneShortcut: KeyboardShortcut? { keyboard.doneShortcut(for: task.id) }

    /// The card's actions, for a right-click. Writes are off with the rest of
    /// the card's while the engine check fails (R2).
    @ViewBuilder
    private var contextMenuItems: some View {
        if task.done {
            Button("Reopen") {
                Task { await runOp(.reopen(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
            }
            .disabled(!writesEnabled)
        } else {
            Button("Mark Done") {
                Task { await runOp(.markDone(subject: task.matchableSubject, shortPrefix: task.shortPrefix)) }
            }
            .disabled(!writesEnabled)
            Menu("Snooze") {
                ForEach(SnoozePopoverView.presets, id: \.label) { preset in
                    Button(preset.label) { snooze(days: preset.days) }
                }
            }
            .disabled(!writesEnabled)
        }
        Divider()
        Button(expanded ? "Collapse" : "Expand") { toggle() }
        Divider()
        Button("Copy") {
            TaskActionsView.copy(task, format: .fullContext, scoutDirectory: scoutDirectory)
        }
        Menu("Copy As") {
            ForEach(ClaudeLauncher.CopyFormat.allCases) { format in
                Button(format.label) {
                    TaskActionsView.copy(task, format: format, scoutDirectory: scoutDirectory)
                }
            }
        }
    }

    private func snooze(days: Int) {
        guard let target = SnoozePopoverView.presetTarget(days: days, from: displayedDate) else { return }
        Task {
            await runOp(.snooze(subject: task.matchableSubject, shortPrefix: task.shortPrefix,
                                until: target, fromKind: kind.rawValue))
        }
    }

    // MARK: - Helpers

    private var isSelected: Bool {
        selection?.wrappedValue.contains(task.id) == true
    }

    private var selectionButton: some View {
        Button {
            guard let selection else { return }
            if isSelected {
                selection.wrappedValue.remove(task.id)
            } else {
                selection.wrappedValue.insert(task.id)
            }
        } label: {
            Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                .font(DS.font(.headline, weight: .medium))
                .foregroundStyle(isSelected ? DS.Accent.ink : DS.Ink.p4)
                .contentTransition(reduceMotion ? .identity : .symbolEffect(.replace))
                .frame(width: 28, height: 28)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plainHit)
        .help(isSelected ? "Remove from copy selection" : "Add to copy selection")
        .accessibilityLabel(isSelected ? "Selected for copying" : "Select for copying")
        .accessibilityValue(isSelected ? "Selected" : "Not selected")
    }

    /// Dispatches a write through `onOp` and threads any failure into the
    /// inline error label. Returns whether the write landed, so editors can
    /// keep their text on failure.
    @discardableResult
    private func runOp(_ op: WriteOp) async -> Bool {
        do {
            try await onOp(op, task.lineNumber)
            await MainActor.run { inlineError = nil }
            return true
        } catch let err as ActionItemsWriterError {
            await MainActor.run { inlineError = describe(err) }
        } catch {
            await MainActor.run { inlineError = error.localizedDescription }
        }
        return false
    }

    /// The error shown under a collapsed card's header. Quick actions (Done,
    /// Snooze, Reopen) run from the collapsed header, and the expanded detail
    /// that normally shows `inlineError` isn't on screen then.
    static func collapsedError(expanded: Bool, inlineError: String?) -> String? {
        expanded ? nil : inlineError
    }

    private func inlineErrorLabel(_ err: String) -> some View {
        Text(err)
            .font(DS.font(.caption))
            .foregroundStyle(DS.Status.err)
            .padding(.top, 2)
            .textSelection(.enabled)
    }

    /// The shared write-problem wording (C10): what happened and what to do.
    /// Raw engine output stays behind the problem's details.
    private func describe(_ err: ActionItemsWriterError) -> String {
        let problem = WriteProblem.from(err, action: "save that change")
        return "\(problem.title). \(problem.message)"
    }

    /// Shared "MMM d" formatter — DateFormatter init is expensive, and this
    /// runs for every card with a snooze pill or carried-from chip in a render
    /// pass. MainActor-bound (module default isolation), so sharing is safe.
    private static let shortDateFormatter: DateFormatter = {
        let fmt = DateFormatter()
        fmt.dateFormat = "MMM d"
        fmt.timeZone = .current
        return fmt
    }()

    private func dateShort(_ d: Date) -> String {
        Self.shortDateFormatter.string(from: d)
    }
}
