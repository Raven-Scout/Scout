// ScoutTests/PerFile/PerFileTabConfigTests.swift
import Testing
@testable import Scout

@Suite("PerFileTabConfig")
struct PerFileTabConfigTests {
    @Test func researchConfig() {
        let c = PerFileTabConfig.research
        #expect(c.priorities == [.urgent, .high, .medium, .low])
        #expect(c.optionalField == .area(label: "Area"))
        #expect(c.directoryDefaultRelative == "knowledge-base/research-queue")
        #expect(c.pathOverrideKey == "researchQueuePath")
        #expect(c.optionalField.label == "Area")
    }
    @Test func priorityVocabPerTab() {
        #expect(PerFileTabConfig.research.priorities == [.urgent, .high, .medium, .low])
    }
}
