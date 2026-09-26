import Foundation

struct SystemLaunchctlClient: LaunchctlClient {
    private let runner: any ProcessRunner
    init(runner: any ProcessRunner) { self.runner = runner }

    func bootout(userUid: uid_t, plistPath: URL) async throws -> Int32 {
        let res = try await runner.run(
            executable: URL(fileURLWithPath: "/bin/launchctl"),
            arguments: ["bootout", "gui/\(userUid)", plistPath.path],
            environment: [:],
            workingDirectory: nil
        )
        return res.exitCode
    }

    func bootstrap(userUid: uid_t, plistPath: URL) async throws {
        let res = try await runner.run(
            executable: URL(fileURLWithPath: "/bin/launchctl"),
            arguments: ["bootstrap", "gui/\(userUid)", plistPath.path],
            environment: [:],
            workingDirectory: nil
        )
        if res.exitCode != 0 {
            let stderr = String(data: res.stderr, encoding: .utf8) ?? ""
            throw LaunchctlError.bootstrapFailed(exitCode: res.exitCode, stderr: stderr)
        }
    }
}
