import Testing
import Foundation
@testable import Scout

@Suite("PowerStateService")
struct PowerStateServiceTests {
    @Test func parseACSampleReturnsOnAC() {
        let sample = """
        Now drawing from 'AC Power'
         -InternalBattery-0 (id=12345)\t100%; charged; 0:00 remaining present: true
        """
        let result = PowerStateService.parsePmsetOutput(sample)
        #expect(result == .onAC)
    }

    @Test func parseBatterySampleReturnsOnBatteryWithLevel() {
        let sample = """
        Now drawing from 'Battery Power'
         -InternalBattery-0 (id=12345)\t73%; discharging; 4:12 remaining present: true
        """
        let result = PowerStateService.parsePmsetOutput(sample)
        #expect(result == .onBattery(level: 0.73))
    }

    @Test func parseBatterySampleAt7Percent() {
        let sample = """
        Now drawing from 'Battery Power'
         -InternalBattery-0 (id=12345)\t7%; discharging; 0:18 remaining present: true
        """
        let result = PowerStateService.parsePmsetOutput(sample)
        #expect(result == .onBattery(level: 0.07))
    }

    @Test func parseEmptyStringReturnsNil() {
        let result = PowerStateService.parsePmsetOutput("")
        #expect(result == nil)
    }

    @Test func parseUnrelatedTextReturnsNil() {
        let result = PowerStateService.parsePmsetOutput("nothing useful here")
        #expect(result == nil)
    }

    @Test func parseBatteryWithoutPercentageReturnsZeroLevel() {
        // Defensive: pmset sometimes prints a header without the percentage
        // line (race during boot, mismatched battery, etc.). Should still
        // recognize "on battery" rather than fall through to .unknown.
        let sample = "Now drawing from 'Battery Power'\nno percentage line follows"
        let result = PowerStateService.parsePmsetOutput(sample)
        #expect(result == .onBattery(level: 0))
    }

    @Test func parseMatchesFirstPercentageWhenMultiplePresent() {
        // Some Macs report multiple batteries; we use the first percentage.
        let sample = """
        Now drawing from 'Battery Power'
         -InternalBattery-0 (id=1)\t52%; discharging; 3:00 remaining
         -InternalBattery-1 (id=2)\t99%; charged; 0:00 remaining
        """
        let result = PowerStateService.parsePmsetOutput(sample)
        #expect(result == .onBattery(level: 0.52))
    }

    @Test @MainActor func startIsIdempotent() {
        // Calling start() twice should not crash and should not orphan a
        // still-firing timer. After stop(), the service is back to a
        // quiescent state. Relies on the invalidate-before-reassign guard
        // in start().
        struct NoopRunner: ProcessRunner {
            func run(
                executable: URL, arguments: [String],
                environment: [String: String], workingDirectory: URL?
            ) async throws -> ProcessResult {
                ProcessResult(exitCode: 0, stdout: Data(), stderr: Data())
            }
        }
        let service = PowerStateService(runner: NoopRunner())
        service.start()
        service.start()
        service.stop()
    }
}
