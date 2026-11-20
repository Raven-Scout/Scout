import Foundation
import Testing
@testable import Scout

/// No raw markup or "run(s)" wording reaches the screen (UI audit T3).
///
/// - The places that show text the app didn't write (engine findings, note
///   snippets, log tails, model-written titles) go through `PlainText`, so a
///   backtick, `**` or table pipe can't come back by someone writing
///   `Text(fix)` again. This scans those files for the bare form.
/// - No Swift string literal spells a plural as "(s)": use `PlainText.count`.
/// - The engine's shared contract fixtures carry no "(s)" either (the engine
///   side is pinned by `plugin/engine/tests/unit/test_plural_wording.py`),
///   and their backticks vanish through `PlainText.codeSpans`.
@Suite("No raw markup in shown text")
struct RawMarkupGuardTests {

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    /// File → the bare form that would show raw text there, and what to use instead.
    static let routed: [(file: String, bare: String, use: String)] = [
        ("Utilities/PlainLanguage/ProblemView.swift", #"Text\((problem\.(title|impact|fix)|impact|fix)\)"#,
         "Text(PlainText.codeSpans(…))"),
        ("Utilities/DSBanner.swift", #"Text\((title|message)\)"#, "Text(PlainText.codeSpans(…))"),
        ("Health/HealthView.swift", #"Text\((model\.summary|note|issue\.title|reason|fix|concern)\)|Label\(concern"#,
         "Text(PlainText.codeSpans(…))"),
        ("Shell/SettingsComponents.swift", #"Text\(help\)"#, "Text(settingsHelp(help))"),
        ("KnowledgeBase/Views/KBRightPanel.swift", #"InlineMarkdownText\(bl\.excerpt\)|Text\(bl\.excerpt\)"#,
         "Text(PlainText.strippingMarkdown(bl.excerpt))"),
        ("ControlCenter/Detail/SummaryTab.swift", #"fallbackPreview = Self\.tail\("#,
         "Self.fallbackPreview(of:)"),
        ("PerFileItems/Views/PerFileItemCardView.swift", #"Text\(item\.title\)"#, "Text(item.displayTitle)"),
        ("PerFileItems/Views/PerFileListView.swift", #"Text\(item\.title\)"#, "Text(item.displayTitle)"),
    ]

    @Test("text the app didn't write goes through PlainText")
    func shownTextIsRouted() throws {
        var hits: [String] = []
        for (file, bare, use) in Self.routed {
            let url = Self.sourceRoot.appendingPathComponent(file)
            let text = try String(contentsOf: url, encoding: .utf8)
            let re = try NSRegularExpression(pattern: bare)
            for (i, line) in text.components(separatedBy: "\n").enumerated()
            where re.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil {
                hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))  → use \(use)")
            }
        }
        #expect(hits.isEmpty, "raw text would show its markup:\n\(hits.joined(separator: "\n"))")
    }

    /// A string literal holding a "(s)"-style plural: "run(s)", "cop(ies)".
    /// Not a call inside an interpolation (`"\(f(s))"`), which a `)` or a
    /// word character follows.
    static let parenPlural = #""[^"\n]*[A-Za-z]\((?:s|es|ies)\)(?![)\w])[^"\n]*""#

    @Test("no Swift source spells a plural as (s)")
    func noParenPlurals() throws {
        let root = Self.sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        let re = try NSRegularExpression(pattern: Self.parenPlural)
        var hits: [String] = []
        var scanned = 0
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            scanned += 1
            let relative = String(url.path.dropFirst(root.path.count + 1))
            let text = try String(contentsOf: url, encoding: .utf8)
            for (i, line) in text.components(separatedBy: "\n").enumerated() {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("//") { continue }
                if re.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil {
                    hits.append("\(relative):\(i + 1): \(trimmed)")
                }
            }
        }
        #expect(scanned > 100, "found only \(scanned) Swift files under \(root.path)")
        #expect(hits.isEmpty, "write real singular and plural (PlainText.count):\n\(hits.joined(separator: "\n"))")
    }

    @Test("the plural guard catches (s) but not code")
    func parenPluralPattern() throws {
        let re = try NSRegularExpression(pattern: Self.parenPlural)
        func hits(_ s: String) -> Bool { re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil }
        #expect(hits(#"Text("\(n) scheduled run(s)")"#))
        #expect(hits(#"lines.append("parked the cop(ies)")"#))
        #expect(!hits(#"Text("\(Self.statusTitle(s))")"#))
        #expect(!hits(#"segmentButton(s)"#))
        #expect(!hits(#"Text(PlainText.count(n, "run"))"#))
    }

    // MARK: Engine fixtures

    private final class FixtureAnchor {}

    static let engineFixtures = ["run-ledger", "doctor-run-health", "connector-alerts", "protection-status"]

    @Test("the engine's shared fixtures say runs, not run(s)", arguments: engineFixtures)
    func engineFixturesHaveNoParenPlurals(_ name: String) throws {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: name, withExtension: "json"))
        let text = try String(contentsOf: url, encoding: .utf8)
        let re = try NSRegularExpression(pattern: #"[A-Za-z]\((?:s|es|ies)\)"#)
        let found = re.matches(in: text, range: NSRange(text.startIndex..., in: text)).map {
            String(text[Range($0.range, in: text)!])
        }
        #expect(found.isEmpty, "\(name).json: \(found)")
    }

    @Test("run-health findings show their commands without backticks")
    func findingsShowNoBackticks() throws {
        let ledger = try RunLedgerContractTests.fixture()
        let findings = try #require(ledger.health?.findings)
        #expect(findings.contains { $0.fix.contains("`") }, "the fixture should exercise a backticked command")
        for finding in findings {
            let problem = Problem(finding: finding)
            for text in [problem.title, problem.impact, problem.fix].compactMap({ $0 }) {
                #expect(!PlainText.codeSpansAsText(text).contains("`"), "\(finding.id): \(text)")
            }
        }
    }
}
