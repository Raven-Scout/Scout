import SwiftUI

/// Left pane: the knowledge-base file tree. Folders expand/collapse; files
/// select into the editor. Custom-styled to match the editorial sidebar rather
/// than using `List`'s blue native selection chrome.
///
/// It works from the keyboard like a Finder outline (UI audit #77): ↑ ↓ Home
/// End move through the rows, opening a note as they land on it; → opens a
/// folder and then steps into it; ← closes it, or steps out to its parent;
/// Return or Space opens or closes a folder.
struct KBTreeView: View {
    let nodes: [KBNode]
    @Binding var selectedPath: String?
    @Binding var expanded: Set<String>
    /// Files whose privacy label is private: they get a lock badge (#453).
    var privatePaths: Set<String> = []

    /// The keyboard's row: a note (then also `selectedPath`) or a folder.
    @State private var cursor: String?
    /// The tree's keyboard focus. Rows are plain buttons, which don't take
    /// focus on a click, so a click sets it and the keys work at once.
    @FocusState private var treeFocused: Bool

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 1) {
                    ForEach(nodes) { node in
                        KBTreeRow(node: node, depth: 0, privatePaths: privatePaths, cursor: cursor,
                                  selectedPath: $selectedPath, expanded: $expanded,
                                  onClick: { cursor = $0; treeFocused = true })
                    }
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 8)
            }
            .focusable()
            .focused($treeFocused)
            .focusEffectDisabled()
            .onKeyPress(phases: [.down, .repeat]) { press in
                guard let key = KBTreeKeyboard.Key(press.key, modifiers: press.modifiers) else { return .ignored }
                let out = KBTreeKeyboard.handle(key, cursor: cursor ?? selectedPath, nodes: nodes, expanded: expanded)
                guard out.changed else { return .ignored }
                expanded = out.expanded
                cursor = out.cursor
                if let open = out.open { selectedPath = open }
                if let row = out.cursor { proxy.scrollTo(row) }
                return .handled
            }
        }
        .background(DS.Paper.sunk.opacity(0.4))
        // A note opened from elsewhere (a link, search, the map) is where the
        // keyboard picks up: its folders open, so its row is drawn and ↓
        // continues from it rather than from the top.
        .onChange(of: selectedPath) { _, path in reveal(path) }
        .onAppear { reveal(selectedPath) }
    }

    private func reveal(_ path: String?) {
        guard let path else { return }
        let folders = KBTreeKeyboard.ancestors(of: path, in: nodes)
        if !expanded.isSuperset(of: folders) { expanded.formUnion(folders) }
        cursor = path
    }
}

/// The tree's outline keys, as a pure function of the tree, so they can be
/// tested without a window.
nonisolated enum KBTreeKeyboard {
    enum Key: Equatable {
        case up, down, home, end, left, right, toggle

        init?(_ key: KeyEquivalent, modifiers: EventModifiers) {
            guard modifiers.isDisjoint(with: [.command, .option, .control, .shift]) else { return nil }
            switch key {
            case .upArrow:          self = .up
            case .downArrow:        self = .down
            case .home:             self = .home
            case .end:              self = .end
            case .leftArrow:        self = .left
            case .rightArrow:       self = .right
            case .return, .space:   self = .toggle
            default:                return nil
            }
        }
    }

    struct Outcome: Equatable {
        var cursor: String?
        var expanded: Set<String>
        /// A note to open.
        var open: String?
        var changed = true

        static let unchanged = Outcome(cursor: nil, expanded: [], open: nil, changed: false)
    }

    /// A row on screen: the node and the folder it sits in.
    struct Row: Equatable {
        let node: KBNode
        let parent: String?
    }

    /// The rows drawn, top to bottom: every top-level node, and the children
    /// of each expanded folder.
    static func visibleRows(_ nodes: [KBNode], expanded: Set<String>, parent: String? = nil) -> [Row] {
        nodes.flatMap { node -> [Row] in
            var rows = [Row(node: node, parent: parent)]
            if node.isDirectory, expanded.contains(node.relativePath) {
                rows += visibleRows(node.children, expanded: expanded, parent: node.relativePath)
            }
            return rows
        }
    }

    /// The folders in the tree that hold `path`, outermost first.
    static func ancestors(of path: String, in nodes: [KBNode]) -> [String] {
        for node in nodes where node.isDirectory && path.hasPrefix(node.relativePath + "/") {
            return [node.relativePath] + ancestors(of: path, in: node.children)
        }
        return []
    }

    static func handle(_ key: Key, cursor: String?, nodes: [KBNode], expanded: Set<String>) -> Outcome {
        let rows = visibleRows(nodes, expanded: expanded)
        let ids = rows.map(\.node.relativePath)
        /// Land on a row; a note opens as the cursor reaches it.
        func moveTo(_ path: String?) -> Outcome {
            guard let path, path != cursor, let row = rows.first(where: { $0.node.relativePath == path })
            else { return .unchanged }
            return Outcome(cursor: path, expanded: expanded, open: row.node.isDirectory ? nil : path)
        }
        switch key {
        case .up:   return moveTo(KeyboardListNavigation.target(from: cursor, in: ids, move: .previous))
        case .down: return moveTo(KeyboardListNavigation.target(from: cursor, in: ids, move: .next))
        case .home: return moveTo(ids.first)
        case .end:  return moveTo(ids.last)
        case .left, .right, .toggle:
            guard let cursor, let row = rows.first(where: { $0.node.relativePath == cursor }) else {
                return key == .toggle ? .unchanged : moveTo(ids.first)
            }
            let node = row.node
            let isOpen = expanded.contains(cursor)
            switch key {
            case .right:
                guard node.isDirectory else { return .unchanged }
                if !isOpen { return Outcome(cursor: cursor, expanded: expanded.union([cursor]), open: nil) }
                return node.children.isEmpty ? .unchanged : moveTo(node.children[0].relativePath)
            case .left:
                if node.isDirectory, isOpen { return Outcome(cursor: cursor, expanded: expanded.subtracting([cursor]), open: nil) }
                guard let parent = row.parent else { return .unchanged }
                return Outcome(cursor: parent, expanded: expanded, open: nil)
            default: // .toggle
                if node.isDirectory {
                    return Outcome(cursor: cursor,
                                   expanded: isOpen ? expanded.subtracting([cursor]) : expanded.union([cursor]),
                                   open: nil)
                }
                return Outcome(cursor: cursor, expanded: expanded, open: cursor)
            }
        }
    }
}

private struct KBTreeRow: View {
    let node: KBNode
    let depth: Int
    let privatePaths: Set<String>
    let cursor: String?
    @Binding var selectedPath: String?
    @Binding var expanded: Set<String>
    let onClick: (String) -> Void

    private var isExpanded: Bool { expanded.contains(node.relativePath) }
    private var isSelected: Bool { selectedPath == node.relativePath }
    private var isCursor: Bool { cursor == node.relativePath }

    var body: some View {
        if node.isDirectory {
            Button { toggle(); onClick(node.relativePath) } label: { dirLabel }
                .buttonStyle(.plainHit)
                .accessibilityValue(isExpanded ? "Expanded" : "Collapsed")
                .accessibilityAddTraits(isCursor ? .isSelected : [])
                .id(node.relativePath)
            if isExpanded {
                ForEach(node.children) { child in
                    KBTreeRow(node: child, depth: depth + 1, privatePaths: privatePaths, cursor: cursor,
                              selectedPath: $selectedPath, expanded: $expanded, onClick: onClick)
                }
            }
        } else {
            Button { selectedPath = node.relativePath; onClick(node.relativePath) } label: { fileLabel }
                .buttonStyle(.plainHit)
                .accessibilityAddTraits(isSelected ? .isSelected : [])
                .id(node.relativePath)
        }
    }

    private var dirLabel: some View {
        HStack(spacing: 6) {
            Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                .font(DS.font(.micro, weight: .semibold))
                .foregroundStyle(DS.Ink.p4)
                .frame(width: 10)
            Image(systemName: isExpanded ? "folder.fill" : "folder")
                .font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
            Text(node.name).font(DS.font(.label, weight: .medium)).foregroundStyle(DS.Ink.p2)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.leading, CGFloat(depth) * 14 + 4)
        .padding(.vertical, 4).padding(.trailing, 6)
        .background {
            // A folder is only ever the keyboard's row, never the open note.
            if isCursor {
                RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Accent.fill.opacity(0.65), lineWidth: 1)
            }
        }
        .contentShape(Rectangle())
    }

    private var fileLabel: some View {
        HStack(spacing: 6) {
            Image(systemName: glyph)
                .font(DS.font(.caption2)).foregroundStyle(isSelected ? DS.Accent.ink : DS.Ink.p4)
                .frame(width: 10)
            Text(node.displayName)
                .font(DS.font(.label))
                .foregroundStyle(isSelected ? DS.Ink.p1 : DS.Ink.p2)
                .lineLimit(1)
            Spacer(minLength: 0)
            if privatePaths.contains(node.relativePath) {
                Image(systemName: "lock.fill")
                    .font(DS.font(.micro)).foregroundStyle(DS.Ink.p4)
                    .help("Private: only you and your own scheduled runs see this note")
                    .accessibilityLabel("Private")
            }
        }
        .padding(.leading, CGFloat(depth) * 14 + 20)
        .padding(.vertical, 4).padding(.trailing, 6)
        .background {
            if isSelected {
                RoundedRectangle(cornerRadius: 6).fill(DS.Accent.wash)
            }
        }
        .contentShape(Rectangle())
    }

    private var glyph: String {
        switch node.ext {
        case "yaml", "yml": return "tablecells"
        default: return "doc.text"
        }
    }

    private func toggle() {
        if isExpanded { expanded.remove(node.relativePath) }
        else { expanded.insert(node.relativePath) }
    }
}
