// ScoutTests/PerFile/PerFileTabConfigTests.swift
import Testing
@testable import Scout

@Suite("PerFileTabConfig")
struct PerFileTabConfigTests {
    @Test func wishlistConfig() {
        let c = PerFileTabConfig.wishlist
        #expect(c.title == "Wishlist")
        #expect(c.priorities == [.high, .medium, .low])    // no urgent
        #expect(c.optionalField == .source(label: "Source"))
        #expect(c.directoryDefaultRelative == "docs/wishlist")
        #expect(c.pathOverrideKey == "wishlistPath")
    }
    @Test func researchConfig() {
        let c = PerFileTabConfig.research
        #expect(c.priorities == [.urgent, .high, .medium, .low])
        #expect(c.optionalField == .area(label: "Area"))
        #expect(c.directoryDefaultRelative == "knowledge-base/research-queue")
        #expect(c.pathOverrideKey == "researchQueuePath")
        #expect(c.optionalField.label == "Area")
    }
}
