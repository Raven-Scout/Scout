import Combine
import Foundation

/// Loads one item's file-scoped git history for `PerFileItemDetailView`, plus
/// each commit's other-files list on demand. Errors surface as `.failed`,
/// never an empty list (#47). One instance per opened item. (#43)
@MainActor
final class PerFileItemActivityModel: ObservableObject {

    enum LoadState: Equatable {
        case loading
        case loaded([FileRevision])
        case unavailable(String)
        case failed(String)
    }

    enum FilesState: Equatable {
        case loading
        case loaded([String])
        case failed(String)
    }

    @Published private(set) var state: LoadState = .loading
    @Published private(set) var otherFiles: [String: FilesState] = [:]

    private let git: GitService
    private let repoURL: URL
    private var relativePath: String?

    init(git: GitService, repoURL: URL) {
        self.git = git
        self.repoURL = repoURL
    }

    /// (Re)load the history. Keeps showing the previous result while a
    /// refresh is in flight; a cancelled load (the item changed again) never
    /// overwrites a newer one.
    func load(_ item: PerFileItem) async {
        guard let rel = ItemActivity.repoRelativePath(of: item.fileURL, repo: repoURL) else {
            state = .unavailable("History is only available for items inside the Scout vault's git repo.")
            return
        }
        relativePath = rel
        do {
            let revisions = try await git.fileHistory(relativePath: rel)
            guard !Task.isCancelled else { return }
            state = .loaded(revisions)
        } catch {
            guard !Task.isCancelled else { return }
            state = .failed("Couldn't load history — \(error.localizedDescription)")
        }
    }

    func loadOtherFiles(for commit: Commit) async {
        switch otherFiles[commit.id] {
        case .loaded, .loading: return
        default: break
        }
        otherFiles[commit.id] = .loading
        do {
            let files = try await git.filesChanged(inCommit: commit.id)
            otherFiles[commit.id] = .loaded(files.filter { $0 != relativePath })
        } catch {
            otherFiles[commit.id] = .failed("Couldn't list the commit's files — \(error.localizedDescription)")
        }
    }
}
