// ScoutTests/PerFile/ProvenanceLineTests.swift
import Foundation
import Testing
@testable import Scout

/// The "Source: …" line on a Research card (UI audit R5).
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
        // Balanced parentheses stay in the URL; the prose's closing one doesn't.
        #expect(PerFileItemCardView.provenanceMarkdown(label: "Source", value: "https://example.com/wiki/Foo_(bar)")
                == "Source: <https://example.com/wiki/Foo_(bar)>")
        #expect(PerFileItemCardView.provenanceMarkdown(label: "Source", value: "Notes (https://example.com/a)")
                == "Source: Notes (<https://example.com/a>)")
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
