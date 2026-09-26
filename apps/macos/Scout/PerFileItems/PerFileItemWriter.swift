import Combine
import Foundation

enum ItemResolution: Sendable, Equatable {
    case done, dropped
    nonisolated var status: ItemStatus { self == .done ? .done : .dropped }
    nonisolated var word: String { self == .done ? "done" : "dropped" }
}

enum PerFileItemWriterError: Error, Equatable {
    case emptyTitle
    case readFailed(String)
    case writeFailed(String)
    case frontmatterNotFound(file: String)
    case fieldNotFound(field: String, file: String)
}

/// Serializes per-file writes (add new item, resolve to done/dropped) and
/// git-commits each change scoped to its single file (best-effort).
actor PerFileItemWriter {
    private let scoutDirectory: URL
    private let gitService: GitServiceProtocol?
    private let now: @Sendable () -> Date
    private var tail: Task<Void, Never>?

    init(scoutDirectory: URL, gitService: GitServiceProtocol?, now: @escaping @Sendable () -> Date = { Date() }) {
        self.scoutDirectory = scoutDirectory
        self.gitService = gitService
        self.now = now
    }

    @discardableResult
    func addItem(title: String, priority: ItemPriority, body: String, source: String?, area: String?,
                 in directoryURL: URL, noun: String) async throws -> URL {
        let previous = tail
        let task = Task { [scoutDirectory, gitService, now] in
            _ = await previous?.value
            return try await Self.performAdd(title: title, priority: priority, body: body, source: source,
                                             area: area, directoryURL: directoryURL, noun: noun,
                                             scoutDirectory: scoutDirectory, gitService: gitService, now: now)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    func resolve(_ resolution: ItemResolution, fileURL: URL, label: String) async throws {
        try await setStatus(resolution.status, fileURL: fileURL, label: label)
    }

    func setStatus(_ status: ItemStatus, fileURL: URL, label: String) async throws {
        let previous = tail
        let message = Self.statusCommitMessage(status, label: label)
        let value = status.frontmatterValue
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performFieldWrite(
                fileURL: fileURL, key: "status", value: value, commitMessage: message,
                scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    static func statusCommitMessage(_ status: ItemStatus, label: String) -> String {
        switch status {
        case .open:        return "app: reopen \(label)"
        case .inProgress:  return "app: start \(label)"
        case .done:        return "app: mark \(label) done"
        case .dropped:     return "app: mark \(label) dropped"
        case .unknown(let raw): return "app: set \(label) status to \(raw)"
        }
    }

    func setPriority(_ priority: ItemPriority, fileURL: URL, label: String) async throws {
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performFieldWrite(
                fileURL: fileURL, key: "priority", value: priority.rawValue,
                commitMessage: "app: set \(label) priority to \(priority.rawValue)",
                scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    // MARK: - perform (off-actor)

    @discardableResult
    private static func performAdd(title: String, priority: ItemPriority, body: String, source: String?,
                                   area: String?, directoryURL: URL, noun: String,
                                   scoutDirectory: URL, gitService: GitServiceProtocol?,
                                   now: @Sendable () -> Date) async throws -> URL {
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty else { throw PerFileItemWriterError.emptyTitle }
        let date = isoDate(now())
        let text = renderItemFile(title: cleanTitle, status: .open, priority: priority, date: date,
                                  source: source?.nilIfBlank, area: area?.nilIfBlank, body: body)
        do { try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true) }
        catch { throw PerFileItemWriterError.writeFailed(error.localizedDescription) }
        let dest = uniqueURL(in: directoryURL, date: date, slug: slugify(cleanTitle))
        do { try text.write(to: dest, atomically: true, encoding: .utf8) }
        catch { throw PerFileItemWriterError.writeFailed(error.localizedDescription) }
        let rel = relativePathInRepo(fileURL: dest, repo: scoutDirectory)
        try? await gitService?.commitPaths([rel], message: "app: add \(noun) \(cleanTitle)")
        return dest
    }

    private static func performFieldWrite(fileURL: URL, key: String, value: String, commitMessage: String,
                                          scoutDirectory: URL, gitService: GitServiceProtocol?) async throws {
        let didWrite: Bool
        do {
            didWrite = try GuardedFileWrite.apply(to: fileURL) { text in
                try rewriteFrontmatterField(text: text, key: key, value: value, file: fileURL.lastPathComponent)
            }
        } catch let e as GuardedFileWrite.Failure {
            switch e {
            case .read(let m): throw PerFileItemWriterError.readFailed(m)
            case .write(let m): throw PerFileItemWriterError.writeFailed(m)
            case .conflictPersisted:
                throw PerFileItemWriterError.writeFailed("\(fileURL.lastPathComponent) changed repeatedly under concurrent writes")
            }
        }
        guard didWrite else { return }
        let rel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        try? await gitService?.commitPaths([rel], message: commitMessage)
    }

    // MARK: - pure helpers

    static func slugify(_ title: String, maxWords: Int = 8) -> String {
        let mapped = title.lowercased().map { ch -> Character in
            (("a"..."z").contains(ch) || ("0"..."9").contains(ch)) ? ch : " "
        }
        let words = String(mapped).split(separator: " ").prefix(maxWords)
        return words.joined(separator: "-")
    }

    static func renderItemFile(title: String, status: ItemStatus, priority: ItemPriority, date: String,
                               source: String?, area: String?, body: String) -> String {
        func yq(_ s: String) -> String {
            "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
        }
        var fm = ["---", "title: \(yq(title))", "status: \(status.frontmatterValue)",
                  "priority: \(priority.rawValue)", "date: \(date)"]
        if let source, !source.isEmpty { fm.append("source: \(yq(source))") }
        if let area, !area.isEmpty { fm.append("area: \(yq(area))") }
        fm.append("---")
        let trimmedBody = body.trimmingCharacters(in: .whitespacesAndNewlines)
        return fm.joined(separator: "\n") + "\n\n# \(title)\n\n" + trimmedBody + "\n"
    }

    static func uniqueURL(in dir: URL, date: String, slug: String) -> URL {
        let base = "\(date)-\(slug)"
        var candidate = dir.appendingPathComponent("\(base).md")
        var n = 2
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = dir.appendingPathComponent("\(base)-\(n).md")
            n += 1
        }
        return candidate
    }

    static func rewriteFrontmatterField(text: String, key: String, value: String, file: String) throws -> String {
        var lines = text.components(separatedBy: "\n")
        guard let first = lines.first, first.trimmingCharacters(in: .whitespaces) == "---" else {
            throw PerFileItemWriterError.frontmatterNotFound(file: file)
        }
        let wantedKey = key.lowercased()
        var i = 1
        while i < lines.count {
            if lines[i].trimmingCharacters(in: .whitespaces) == "---" { break }
            if let colon = lines[i].firstIndex(of: ":") {
                let k = lines[i][..<colon].trimmingCharacters(in: .whitespaces).lowercased()
                if k == wantedKey {
                    let leading = String(lines[i].prefix(while: { $0 == " " || $0 == "\t" }))
                    lines[i] = "\(leading)\(key): \(value)"
                    return lines.joined(separator: "\n")
                }
            }
            i += 1
        }
        throw PerFileItemWriterError.fieldNotFound(field: key, file: file)
    }

    private static func isoDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone.current
        return f.string(from: date)
    }

    private static func relativePathInRepo(fileURL: URL, repo: URL) -> String {
        let full = fileURL.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : fileURL.lastPathComponent
    }
}

/// Actors can't be `@EnvironmentObject`; wrap for SwiftUI injection.
final class PerFileItemWriterBox: ObservableObject {
    let writer: PerFileItemWriter
    init(writer: PerFileItemWriter) { self.writer = writer }
}

private extension String {
    nonisolated var nilIfBlank: String? { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self }
}
