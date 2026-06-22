// ScoutTests/PerFile/ItemStatusPriorityTests.swift
import Testing
@testable import Scout

@Suite("ItemStatus & ItemPriority")
struct ItemStatusPriorityTests {
    @Test func statusParsesKnownAndDefaultsAndUnknown() {
        #expect(ItemStatus.parse("open") == .open)
        #expect(ItemStatus.parse("in-progress") == .inProgress)
        #expect(ItemStatus.parse("in progress") == .inProgress)
        #expect(ItemStatus.parse("done") == .done)
        #expect(ItemStatus.parse("dropped") == .dropped)
        #expect(ItemStatus.parse("") == .open)              // missing -> open
        #expect(ItemStatus.parse("weird") == .unknown("weird"))
    }
    @Test func statusActiveSplit() {
        #expect(ItemStatus.open.isActive)
        #expect(ItemStatus.inProgress.isActive)
        #expect(!ItemStatus.done.isActive)
        #expect(!ItemStatus.dropped.isActive)
        #expect(!ItemStatus.unknown("x").isActive)
    }
    @Test func statusFrontmatterValue() {
        #expect(ItemStatus.open.frontmatterValue == "open")
        #expect(ItemStatus.inProgress.frontmatterValue == "in-progress")
        #expect(ItemStatus.done.frontmatterValue == "done")
        #expect(ItemStatus.dropped.frontmatterValue == "dropped")
    }
    @Test func priorityParsesAndDefaultsMedium() {
        #expect(ItemPriority.parse("urgent") == .urgent)
        #expect(ItemPriority.parse("high") == .high)
        #expect(ItemPriority.parse("low") == .low)
        #expect(ItemPriority.parse("medium") == .medium)
        #expect(ItemPriority.parse("") == .medium)          // missing -> medium
        #expect(ItemPriority.parse("bogus") == .medium)     // unknown -> medium
    }
    @Test func priorityOrderingUrgentHighest() {
        #expect(ItemPriority.urgent < ItemPriority.high)
        #expect(ItemPriority.high < ItemPriority.medium)
        #expect(ItemPriority.medium < ItemPriority.low)
        #expect([ItemPriority.low, .urgent, .medium, .high].sorted() == [.urgent, .high, .medium, .low])
    }
}
