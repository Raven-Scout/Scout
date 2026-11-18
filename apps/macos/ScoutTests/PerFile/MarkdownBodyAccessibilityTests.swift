// ScoutTests/PerFile/MarkdownBodyAccessibilityTests.swift
import SwiftUI
import Testing
@testable import Scout

/// What VoiceOver reads for a quote or callout in a card body (UI audit R5):
/// the kind first, then the title and the body, read from the real
/// accessibility tree.
@MainActor
@Suite("MarkdownBodyView accessibility", .serialized)
struct MarkdownBodyAccessibilityTests {
    private func labels(_ blocks: [MarkdownBodyBlock]) async -> [String] {
        await AccessibilityTree.nodes(of: MarkdownBodyView(blocks: blocks).frame(width: 600)).map(\.label)
    }

    @Test("a callout reads its kind, its title and its body")
    func calloutReadsTitleAndBody() async {
        let all = await labels([.quote(kind: "warning", title: "Check the window", text: "It moved to the afternoon.")])
        let node = all.first { $0.contains("Check the window") }
        #expect(node != nil, "\(all)")
        #expect(node?.hasPrefix("Warning: ") == true, "\(all)")
        #expect(node?.contains("It moved to the afternoon.") == true, "\(all)")
    }

    @Test("a plain quote reads \"Quote\" and then its text")
    func quoteReadsItsText() async {
        let all = await labels([.quote(kind: nil, title: nil, text: "Ship it on Friday.")])
        let node = all.first { $0.contains("Ship it on Friday.") }
        #expect(node != nil, "\(all)")
        #expect(node?.hasPrefix("Quote: ") == true, "\(all)")
    }
}
