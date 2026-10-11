import Foundation

/// The folder a local Claude Code session ran in, read from its transcript
/// (`~/.claude/projects/<encoded folder>/<id>.jsonl`, whose lines carry
/// `cwd`). `claude --resume <id>` only finds a session from that folder.
nonisolated enum ClaudeSessionFolder {
    static let headBytes = 256 * 1024

    static var productionClaudeHome: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".claude", isDirectory: true)
    }

    static func folder(forSessionID id: String, claudeHome: URL = productionClaudeHome) -> URL? {
        let projects = claudeHome.appendingPathComponent("projects", isDirectory: true)
        guard let dirs = try? FileManager.default.contentsOfDirectory(at: projects, includingPropertiesForKeys: nil) else { return nil }
        for dir in dirs {
            let transcript = dir.appendingPathComponent("\(id).jsonl")
            if let cwd = cwd(inTranscript: transcript) { return cwd }
        }
        return nil
    }

    static func cwd(inTranscript url: URL) -> URL? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        guard let data = try? handle.read(upToCount: headBytes) else { return nil }
        for line in data.split(separator: UInt8(ascii: "\n")) {
            guard let object = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any],
                  let path = object["cwd"] as? String else { continue }
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory), isDirectory.boolValue else { return nil }
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return nil
    }
}
