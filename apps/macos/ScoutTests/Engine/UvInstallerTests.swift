import Testing
import Foundation
import CryptoKit
@testable import Scout

@Suite("UvInstaller")
struct UvInstallerTests {
    struct StubDownloader: FileDownloader {
        let file: URL
        func download(_ url: URL) async throws -> URL { file }
    }

    func layout() throws -> EngineLayout {
        let l = EngineLayout(home: FileManager.default.temporaryDirectory.appendingPathComponent("uv-\(UUID().uuidString)"))
        try FileManager.default.createDirectory(at: l.localBin, withIntermediateDirectories: true)
        return l
    }

    /// Build `uv-<arch>.tar.gz` containing `uv-<arch>/uv` the way astral ships it.
    func fakeTarball(arch: UvArch, in dir: URL) throws -> (URL, String) {
        let stage = dir.appendingPathComponent("stage/uv-\(arch.rawValue)")
        try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
        try "#!/bin/sh\necho uv 0.12.1\n".write(to: stage.appendingPathComponent("uv"), atomically: true, encoding: .utf8)
        let tar = dir.appendingPathComponent("uv-\(arch.rawValue).tar.gz")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        p.arguments = ["-czf", tar.path, "-C", dir.appendingPathComponent("stage").path, "uv-\(arch.rawValue)"]
        try p.run(); p.waitUntilExit()
        let sha = SHA256.hash(data: try Data(contentsOf: tar)).map { String(format: "%02x", $0) }.joined()
        return (tar, sha)
    }

    @Test func assetURLFollowsAstralNaming() {
        #expect(UvInstaller.assetURL(version: "0.12.1", arch: .arm64).absoluteString == "https://github.com/astral-sh/uv/releases/download/0.12.1/uv-aarch64-apple-darwin.tar.gz")
    }

    // systemCandidates is always [] here — a real /opt/homebrew/bin/uv or
    // /usr/local/bin/uv on the host or CI runner must never leak into a
    // result these tests assert on (Ruling 49). layout.uvURL inside the
    // test's temp home is still checked first by `existing()`.
    @Test func existingUvShortCircuits() async throws {
        let l = try layout()
        defer { try? FileManager.default.removeItem(at: l.home) }
        try "#!/bin/sh\n".write(to: l.uvURL, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: l.uvURL.path)
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: [:]), layout: l, downloader: StubDownloader(file: URL(fileURLWithPath: "/nonexistent")), runner: SystemProcessRunner(), arch: .arm64, systemCandidates: [])
        #expect(try await installer.ensure(log: { _ in }) == l.uvURL)
    }

    @Test func downloadsVerifiesAndInstalls() async throws {
        let l = try layout()
        defer { try? FileManager.default.removeItem(at: l.home) }
        let (tar, sha) = try fakeTarball(arch: .arm64, in: l.home)
        let logs = LogRecorder()
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: ["aarch64-apple-darwin": sha]), layout: l,
                                    downloader: StubDownloader(file: tar), runner: SystemProcessRunner(), arch: .arm64, systemCandidates: [])
        let uv = try await installer.ensure(log: { logs.append($0) })
        #expect(uv == l.uvURL)
        #expect(FileManager.default.isExecutableFile(atPath: uv.path))
        #expect(logs.messages.contains { $0.contains("sha256 ok") })
    }

    @Test func checksumMismatchInstallsNothing() async throws {
        let l = try layout()
        defer { try? FileManager.default.removeItem(at: l.home) }
        let (tar, _) = try fakeTarball(arch: .arm64, in: l.home)
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: ["aarch64-apple-darwin": String(repeating: "0", count: 64)]), layout: l,
                                    downloader: StubDownloader(file: tar), runner: SystemProcessRunner(), arch: .arm64, systemCandidates: [])
        await #expect(throws: UvInstallerError.self) { try await installer.ensure(log: { _ in }) }
        #expect(!FileManager.default.fileExists(atPath: l.uvURL.path))
    }

    @Test func missingChecksumForArchIsAnError() async throws {
        let l = try layout()
        defer { try? FileManager.default.removeItem(at: l.home) }
        let installer = UvInstaller(release: .init(version: "0.12.1", sha256: [:]), layout: l, downloader: StubDownloader(file: URL(fileURLWithPath: "/x")), runner: SystemProcessRunner(), arch: .x86_64, systemCandidates: [])
        await #expect(throws: UvInstallerError.missingChecksum("x86_64-apple-darwin")) { try await installer.ensure(log: { _ in }) }
    }
}

/// Thread-safe recorder for the `@Sendable (String) -> Void` log closure —
/// a plain captured `var logs: [String]` can't be mutated from a `@Sendable`
/// closure under Swift 6 strict concurrency.
private final class LogRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var _messages: [String] = []

    func append(_ message: String) {
        lock.withLock { _messages.append(message) }
    }

    var messages: [String] { lock.withLock { _messages } }
}
