import Testing
@testable import Scout

/// Pure-text coverage for `UpdateTrackRow`'s version line, extracted to
/// `UpdateRowFormatting` so it's testable without standing up a view
/// hierarchy — Settings ▸ Updates itself is verified by build + the whole
/// suite, consistent with the rest of the Settings sections.
@Suite("Update row formatting")
struct UpdateRowFormattingTests {
    @Test("available — shows current → latest")
    func available() {
        #expect(UpdateRowFormatting.versionLine(currentVersion: "0.10.0", latestVersion: "0.11.0", state: .available)
            == "0.10.0 → 0.11.0 available")
    }

    @Test("available with no latest version yet — falls back to a placeholder")
    func availableNoLatest() {
        #expect(UpdateRowFormatting.versionLine(currentVersion: "0.10.0", latestVersion: nil, state: .available)
            == "0.10.0 → ? available")
    }

    @Test("up to date")
    func upToDate() {
        #expect(UpdateRowFormatting.versionLine(currentVersion: "0.11.0", latestVersion: nil, state: .upToDate)
            == "0.11.0 · up to date")
    }

    @Test("checking")
    func checking() {
        #expect(UpdateRowFormatting.versionLine(currentVersion: "0.11.0", latestVersion: nil, state: .checking)
            == "0.11.0 · checking…")
    }

    @Test("error — message is not shown inline")
    func error() {
        #expect(UpdateRowFormatting.versionLine(currentVersion: "0.11.0", latestVersion: nil, state: .error("boom"))
            == "0.11.0 · last check failed")
    }

    @Test("idle — just the current version")
    func idle() {
        #expect(UpdateRowFormatting.versionLine(currentVersion: "0.11.0", latestVersion: nil, state: .idle)
            == "0.11.0")
    }

    @Test("no current version — em dash placeholder")
    func noCurrentVersion() {
        #expect(UpdateRowFormatting.versionLine(currentVersion: nil, latestVersion: nil, state: .idle) == "—")
    }
}
