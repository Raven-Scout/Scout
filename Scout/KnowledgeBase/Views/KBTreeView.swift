import SwiftUI

/// Left pane: the knowledge-base file tree. Folders expand/collapse; files
/// select into the editor. Custom-styled to match the editorial sidebar rather
/// than using `List`'s blue native selection chrome.
struct KBTreeView: View {
    let nodes: [KBNode]
    @Binding var selectedPath: String?
    @Binding var expanded: Set<String>

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 1) {
                ForEach(nodes) { node in
                    KBTreeRow(node: node, depth: 0,
                              selectedPath: $selectedPath, expanded: $expanded)
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 8)
        }
        .background(DS.Paper.sunk.opacity(0.4))
    }
}

private struct KBTreeRow: View {
    let node: KBNode
    let depth: Int
    @Binding var selectedPath: String?
    @Binding var expanded: Set<String>

    private var isExpanded: Bool { expanded.contains(node.relativePath) }
    private var isSelected: Bool { selectedPath == node.relativePath }

    var body: some View {
        if node.isDirectory {
            Button { toggle() } label: { dirLabel }.buttonStyle(.plainHit)
            if isExpanded {
                ForEach(node.children) { child in
                    KBTreeRow(node: child, depth: depth + 1,
                              selectedPath: $selectedPath, expanded: $expanded)
                }
            }
        } else {
            Button { selectedPath = node.relativePath } label: { fileLabel }
                .buttonStyle(.plainHit)
        }
    }

    private var dirLabel: some View {
        HStack(spacing: 6) {
            Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(DS.Ink.p4)
                .frame(width: 10)
            Image(systemName: isExpanded ? "folder.fill" : "folder")
                .font(.system(size: 11)).foregroundStyle(DS.Ink.p3)
            Text(node.name).font(DS.sans(12.5, weight: .medium)).foregroundStyle(DS.Ink.p2)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.leading, CGFloat(depth) * 14 + 4)
        .padding(.vertical, 4).padding(.trailing, 6)
        .contentShape(Rectangle())
    }

    private var fileLabel: some View {
        HStack(spacing: 6) {
            Image(systemName: glyph)
                .font(.system(size: 10)).foregroundStyle(isSelected ? DS.Accent.ink : DS.Ink.p4)
                .frame(width: 10)
            Text(node.displayName)
                .font(DS.sans(12.5))
                .foregroundStyle(isSelected ? DS.Ink.p1 : DS.Ink.p2)
                .lineLimit(1)
            Spacer(minLength: 0)
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
