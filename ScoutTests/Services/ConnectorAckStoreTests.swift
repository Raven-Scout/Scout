import Testing
import Foundation
@testable import Scout

@Suite("ConnectorAckStore")
struct ConnectorAckStoreTests {
    @Test func ackPersistsAndRoundTrips() throws {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }

        let a = ConnectorAckStore(fileURL: url)
        a.ack(fingerprint: "foo")
        #expect(a.isAcked("foo"))

        // New instance reads from disk.
        let b = ConnectorAckStore(fileURL: url)
        #expect(b.isAcked("foo"))
        #expect(!b.isAcked("bar"))
    }

    @Test func gcRemovesFingerprintsNotInActiveSet() throws {
        let url = tempURL()
        defer { try? FileManager.default.removeItem(at: url) }

        let store = ConnectorAckStore(fileURL: url)
        store.ack(fingerprint: "alive")
        store.ack(fingerprint: "stale")
        store.gc(active: ["alive"])
        #expect(store.isAcked("alive"))
        #expect(!store.isAcked("stale"))
    }

    @Test func missingFileBehavesAsEmpty() {
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent("does-not-exist-\(UUID().uuidString).json")
        let store = ConnectorAckStore(fileURL: missing)
        #expect(!store.isAcked("anything"))
    }

    private func tempURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".json")
    }
}
