import Foundation
import CryptoKit

/// Downloads a file to a local temporary location.
protocol FileDownloader: Sendable {
    /// Download to a temporary file and return its URL.
    func download(_ url: URL) async throws -> URL
}

/// Production `FileDownloader`. Never exercised by tests — no network in tests.
nonisolated struct URLSessionDownloader: FileDownloader {
    func download(_ url: URL) async throws -> URL {
        let (tmp, response) = try await URLSession.shared.download(from: url)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return tmp
    }
}

nonisolated enum UvArch: String, Sendable {
    case arm64 = "aarch64-apple-darwin"
    case x86_64 = "x86_64-apple-darwin"

    static var current: UvArch {
        #if arch(arm64)
        return .arm64
        #else
        return .x86_64
        #endif
    }
}

nonisolated enum UvInstallerError: Error, Equatable {
    case missingChecksum(String)
    case checksumMismatch(expected: String, actual: String)
    case binaryNotInArchive
    case extractFailed(String)
}

/// Obtains `uv` (spec §4.3): reuse one that exists, else download the pinned
/// release for this architecture, verify SHA-256 against the pin baked into
/// the app, and install it to `~/.local/bin/uv`.
nonisolated struct UvInstaller: Sendable {
    let release: EngineRelease.Uv
    let layout: EngineLayout
    let downloader: any FileDownloader
    let runner: any ProcessRunner
    var arch: UvArch = .current
    /// Injectable so tests never depend on what this Mac or CI runner has
    /// installed at these real paths (Ruling 49). Production uses the real
    /// candidate paths; every test passes `[]`.
    var systemCandidates: [String] = UvInstaller.defaultSystemCandidates

    static let defaultSystemCandidates = ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"]

    static func assetURL(version: String, arch: UvArch) -> URL {
        URL(string: "https://github.com/astral-sh/uv/releases/download/\(version)/uv-\(arch.rawValue).tar.gz")!
    }

    init(
        release: EngineRelease.Uv,
        layout: EngineLayout,
        downloader: any FileDownloader,
        runner: any ProcessRunner,
        arch: UvArch = .current,
        systemCandidates: [String] = UvInstaller.defaultSystemCandidates
    ) {
        self.release = release
        self.layout = layout
        self.downloader = downloader
        self.runner = runner
        self.arch = arch
        self.systemCandidates = systemCandidates
    }

    /// `layout.uvURL` inside a test's temp home is always checked first, so
    /// tests never depend on the real-path candidates even when they aren't
    /// injected empty.
    func existing() -> URL? {
        if FileManager.default.isExecutableFile(atPath: layout.uvURL.path) { return layout.uvURL }
        return systemCandidates.first { FileManager.default.isExecutableFile(atPath: $0) }.map { URL(fileURLWithPath: $0) }
    }

    func ensure(log: @Sendable (String) -> Void) async throws -> URL {
        if let uv = existing() { log("uv present at \(uv.path)"); return uv }
        guard let expected = release.sha256[arch.rawValue]?.lowercased() else { throw UvInstallerError.missingChecksum(arch.rawValue) }
        let url = Self.assetURL(version: release.version, arch: arch)
        log("downloading \(url.lastPathComponent)")
        let file = try await downloader.download(url)
        let actual = SHA256.hash(data: try Data(contentsOf: file)).map { String(format: "%02x", $0) }.joined()
        guard actual == expected else { throw UvInstallerError.checksumMismatch(expected: expected, actual: actual) }
        log("sha256 ok")
        let fileManager = FileManager.default
        let stage = fileManager.temporaryDirectory.appendingPathComponent("uv-extract-\(UUID().uuidString)")
        try fileManager.createDirectory(at: stage, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: stage) }
        let tar = try await runner.run(executable: URL(fileURLWithPath: "/usr/bin/tar"), arguments: ["-xzf", file.path, "-C", stage.path], environment: [:], workingDirectory: nil)
        guard tar.exitCode == 0 else { throw UvInstallerError.extractFailed(String(data: tar.stderr, encoding: .utf8) ?? "") }
        let binary = stage.appendingPathComponent("uv-\(arch.rawValue)/uv")
        guard fileManager.fileExists(atPath: binary.path) else { throw UvInstallerError.binaryNotInArchive }
        try fileManager.createDirectory(at: layout.localBin, withIntermediateDirectories: true)
        if fileManager.fileExists(atPath: layout.uvURL.path) { try fileManager.removeItem(at: layout.uvURL) }
        try fileManager.moveItem(at: binary, to: layout.uvURL)
        try fileManager.setAttributes([.posixPermissions: 0o755], ofItemAtPath: layout.uvURL.path)
        log("installed uv \(release.version) → \(layout.uvURL.path)")
        return layout.uvURL
    }
}
