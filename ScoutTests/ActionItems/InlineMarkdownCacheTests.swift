import Testing
import Foundation
@testable import Scout

// @MainActor is load-bearing here for the same reason it is on
// `InlineMarkdownTextTests` — the static cache is MainActor-isolated in the app
// module, and an unannotated suite races the test host's main-thread renders.
@MainActor
@Suite("Inline markdown cache — LRU eviction")
struct InlineMarkdownCacheTests {

    /// Shrink the cap so the test drives eviction in milliseconds instead of
    /// parsing thousands of strings, and leave it as found.
    private func withCap(_ cap: Int, _ body: () -> Void) {
        let original = InlineMarkdownText.cacheCap
        InlineMarkdownText.resetCacheForTesting()
        InlineMarkdownText.cacheCap = cap
        defer {
            InlineMarkdownText.cacheCap = original
            InlineMarkdownText.resetCacheForTesting()
        }
        body()
    }

    @Test("A repeatedly used entry survives eviction of a much larger cold set")
    func hotEntrySurvivesEviction() {
        withCap(50) {
            let hot = "the hot string PROJ-1234 **bold** and `code`"
            _ = InlineMarkdownText.attributedString(for: hot)

            // Ten times the cap in cold traffic, with the hot string touched
            // regularly throughout. The old policy evicted `cache.keys.prefix`
            // — arbitrary dictionary order — so the hot entry was as likely to
            // be dropped as any other, which is what made the cliff a cliff.
            for i in 0 ..< 500 {
                _ = InlineMarkdownText.attributedString(for: "cold entry number \(i)")
                if i % 5 == 0 {
                    _ = InlineMarkdownText.attributedString(for: hot)
                }
            }

            #expect(InlineMarkdownText.cacheContainsForTesting(hot))
        }
    }

    @Test("The cache stays bounded by its cap")
    func staysBounded() {
        withCap(50) {
            for i in 0 ..< 500 {
                _ = InlineMarkdownText.attributedString(for: "entry \(i)")
            }
            #expect(InlineMarkdownText.cacheCountForTesting <= 50)
        }
    }

    @Test("Eviction drops the coldest entries, not the most recent")
    func evictsColdestFirst() {
        withCap(20) {
            // Fill past the cap with a known access order: `recent` is touched
            // last, `ancient` first and never again.
            let ancient = "ancient entry"
            _ = InlineMarkdownText.attributedString(for: ancient)
            for i in 0 ..< 60 {
                _ = InlineMarkdownText.attributedString(for: "filler \(i)")
            }
            let recent = "recent entry"
            _ = InlineMarkdownText.attributedString(for: recent)

            #expect(InlineMarkdownText.cacheContainsForTesting(recent))
            #expect(!InlineMarkdownText.cacheContainsForTesting(ancient))
        }
    }

    @Test("A cache hit returns the same rendering as a cold parse")
    func hitMatchesMiss() {
        withCap(50) {
            let s = "**Bold** with [[people/alex]] and example-org/scout#42"
            let cold = InlineMarkdownText.attributedString(for: s)
            let warm = InlineMarkdownText.attributedString(for: s)
            #expect(String(cold.characters) == String(warm.characters))
            #expect(cold == warm)
        }
    }
}
