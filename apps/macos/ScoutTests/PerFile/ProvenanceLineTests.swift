// ScoutTests/PerFile/ProvenanceLineTests.swift
import Foundation
import Testing
@testable import Scout

/// The "Source: …" line on a Wishlist or Research card (UI audit R5).
@Suite("Card provenance line")
@MainActor
struct ProvenanceLineTests {
    @Test func wikilinksAndMarkdownLinksPassThroughForTheInlineRenderer() {
        #expect(PerFileItemCardView.provenanceMarkdown(label: "Source", value: "[[meetings/2026-01-05-sync|Sync]]")
                == "Source: [[meetings/2026-01-05-sync|Sync]]")
        #expect(PerFileItemCardView.provenanceMarkdown(label: "Source", value: "[the thread](https://example.com/t)")
                == "Source: [the thread](https://example.com/t)")
    }

    @Test func aBareURLBecomesAnAutolink() {
        #expect(PerFileItemCardView.provenanceMarkdown(
            label: "Source", value: "Slack https://acme-co.slack.com/archives/C0123456789/p1700000000000000.")
                == "Source: Slack <https://acme-co.slack.com/archives/C0123456789/p1700000000000000>.")
        // Already linked: left alone.
        #expect(PerFileItemCardView.provenanceMarkdown(label: "Source", value: "<https://example.com>")
                == "Source: <https://example.com>")
    }

    @Test func theRenderedLineCarriesClickableLinks() {
        let md = PerFileItemCardView.provenanceMarkdown(label: "Source", value: "https://example.com/a and [[projects/demo]]")
        let rendered = InlineMarkdownText.attributedString(for: md)
        let links = rendered.runs.compactMap(\.link).map(\.absoluteString)
        #expect(links.contains("https://example.com/a"))
        #expect(links.contains { $0.hasPrefix("scout-wiki://") })
    }
}
