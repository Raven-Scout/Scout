import Foundation

/// One node in the knowledge-base file tree: either a directory (with children)
/// or an editable file. Built by `KnowledgeBaseService` from the on-disk
/// `~/Scout/knowledge-base/` tree; consumed by `KBTreeView`.
///
/// `id` is the path relative to the scout directory so selection survives a
/// reparse (FSEvents reload) as long as the file still exists at the same path.
nonisolated struct KBNode: Identifiable, Equatable, Hashable {
    enum Kind: Equatable, Hashable { case directory, file }

    let kind: Kind
    /// Absolute on-disk location.
    let url: URL
    /// Path relative to the scout directory, e.g. `knowledge-base/people.md`.
    let relativePath: String
    /// Display name (last path component).
    let name: String
    /// Lowercased file extension without the dot (`md`, `yaml`, `yml`), or "" for dirs.
    let ext: String
    /// Sorted children — empty for files.
    let children: [KBNode]

    var id: String { relativePath }

    var isDirectory: Bool { kind == .directory }

    /// Name with the markdown/yaml extension stripped for display in the tree.
    var displayName: String {
        guard kind == .file else { return name }
        switch ext {
        case "md", "yaml", "yml": return (name as NSString).deletingPathExtension
        default: return name
        }
    }

    /// True when this file can be opened in the source editor (text formats).
    var isEditable: Bool {
        kind == .file && ["md", "yaml", "yml"].contains(ext)
    }

    /// Recursively collect every file node under this subtree (depth-first).
    var allFiles: [KBNode] {
        switch kind {
        case .file: return [self]
        case .directory: return children.flatMap(\.allFiles)
        }
    }

    /// Display name (extension stripped) for an arbitrary repo-relative path —
    /// used by graph/backlink code that works with paths, not nodes.
    static func displayName(forPath relPath: String) -> String {
        let base = (relPath as NSString).lastPathComponent
        return (base as NSString).deletingPathExtension
    }
}
