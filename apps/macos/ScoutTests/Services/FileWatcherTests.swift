import Testing
import Foundation
@testable import Scout

@Suite("FileWatcher")
struct FileWatcherTests {
    @Test func emitsEventOnFileCreation() async throws {
        let tmp = try FileManager.default.url(
            for: .itemReplacementDirectory,
            in: .userDomainMask,
            appropriateFor: FileManager.default.temporaryDirectory,
            create: true
        )
        defer { try? FileManager.default.removeItem(at: tmp) }

        let watcher = FileWatcher()
        let stream = watcher.events(for: tmp)

        // Give FSEvents a moment to arm before we touch files
        try await Task.sleep(nanoseconds: 300_000_000)

        // Race: collect events until we see something or time out
        let collected: FileSystemEvent? = await withTaskGroup(of: FileSystemEvent?.self) { group in
            group.addTask {
                for await event in stream { return event }
                return nil
            }
            group.addTask {
                try? await Task.sleep(nanoseconds: 3_000_000_000) // 3s ceiling
                return nil
            }
            // Trigger after consumer is ready
            Task {
                try? await Task.sleep(nanoseconds: 200_000_000)
                try? "hi".write(
                    to: tmp.appendingPathComponent("hello.txt"),
                    atomically: true, encoding: .utf8
                )
            }
            defer { group.cancelAll() }
            return await group.next() ?? nil
        }

        #expect(collected != nil, "expected at least one FS event after creating a file")
    }
}
