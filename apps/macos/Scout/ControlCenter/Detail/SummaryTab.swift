import SwiftUI

/// Renders the markdown body that Scout writes at the end of every successful
/// run — the same block the user gets in their Slack thread. We snip it out
/// of the run's log file and hand it to SwiftUI's native markdown renderer so
/// it reads like prose, not a transcript.
struct SummaryTab: View {
    let logPath: URL
    @State private var summary: String? = nil
    @State private var fallbackPreview: String = ""
    @State private var loadFailed = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let s = summary, !s.isEmpty {
                    summaryView(s)
                } else if loadFailed {
                    Text("Could not read the run log.")
                        .foregroundStyle(.secondary)
                        .italic()
                } else if !fallbackPreview.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("No summary block found in this log.")
                            .foregroundStyle(.secondary)
                            .italic()
                        Text("Showing the last lines so you can still see what happened:")
                            .font(DS.font(.caption2))
                            .foregroundStyle(.secondary)
                        Text(fallbackPreview)
                            .font(DS.font(.body, .mono))
                            .textSelection(.enabled)
                            .padding(8)
                            .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
                    }
                } else {
                    Text("Loading…").foregroundStyle(.secondary)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task(id: logPath) { await load() }
    }

    @ViewBuilder
    private func summaryView(_ markdown: String) -> some View {
        // SwiftUI's AttributedString markdown renderer handles bold, italics,
        // and inline links. It doesn't natively render lists or headings,
        // so we split blocks ourselves and render lists as native VStacks.
        let blocks = splitBlocks(markdown)
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                renderBlock(block)
            }
        }
    }

    private enum Block { case paragraph(String), bullet([String]), heading(String) }

    private func splitBlocks(_ text: String) -> [Block] {
        var blocks: [Block] = []
        var paragraph = ""
        var bullets: [String] = []

        func flushParagraph() {
            let trimmed = paragraph.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { blocks.append(.paragraph(trimmed)) }
            paragraph = ""
        }
        func flushBullets() {
            if !bullets.isEmpty { blocks.append(.bullet(bullets)); bullets = [] }
        }

        for raw in text.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty {
                flushParagraph()
                flushBullets()
                continue
            }
            if line.hasPrefix("# ") || line.hasPrefix("## ") || line.hasPrefix("### ") {
                flushParagraph()
                flushBullets()
                let trimmedHash = line.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                blocks.append(.heading(trimmedHash))
                continue
            }
            if line.hasPrefix("- ") || line.hasPrefix("* ") {
                flushParagraph()
                bullets.append(String(line.dropFirst(2)))
                continue
            }
            // Numbered list ("1. foo") — treat as bullet for clean rendering.
            if let firstSpace = line.firstIndex(of: " "),
               line[..<firstSpace].allSatisfy({ $0.isNumber || $0 == "." }) {
                flushParagraph()
                bullets.append(String(line[line.index(after: firstSpace)...]))
                continue
            }
            flushBullets()
            if !paragraph.isEmpty { paragraph += "\n" }
            paragraph += line
        }
        flushParagraph()
        flushBullets()
        return blocks
    }

    @ViewBuilder
    private func renderBlock(_ block: Block) -> some View {
        switch block {
        case .heading(let text):
            Text(attributed(text))
                .font(DS.font(.headline))
                .fontWeight(.semibold)
        case .paragraph(let text):
            Text(attributed(text))
                .font(DS.font(.body))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        case .bullet(let items):
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .top, spacing: 8) {
                        Text("•")
                            .foregroundStyle(.secondary)
                        Text(attributed(item))
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func attributed(_ s: String) -> AttributedString {
        if let attr = try? AttributedString(
            markdown: s,
            options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return attr
        }
        return AttributedString(s)
    }

    // MARK: - Load

    /// What a run log shows: its summary block, else its last lines.
    nonisolated struct Loaded: Sendable {
        let summary: String?
        let fallbackPreview: String
    }

    private func load() async {
        // Reading the log and stripping its tail happen off the main actor:
        // the log is any size and the tail is untrusted text.
        let url = logPath
        let loaded = await Task.detached(priority: .userInitiated) { Self.read(url) }.value
        guard !Task.isCancelled else { return }
        if let loaded {
            summary = loaded.summary
            fallbackPreview = loaded.fallbackPreview
            loadFailed = false
        } else {
            loadFailed = true
        }
    }

    /// The log at `url`, or nil when it can't be read.
    nonisolated static func read(_ url: URL) -> Loaded? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        let text = String(data: data, encoding: .utf8) ?? ""
        let extracted = extractSummary(from: text)
        return Loaded(summary: extracted, fallbackPreview: extracted == nil ? fallbackPreview(of: text) : "")
    }

    /// Pull the body from the first `**SCOUT … complete…` (or `Run summary`)
    /// marker until the trailing run-finished banner.
    nonisolated static func extractSummary(from text: String) -> String? {
        let lines = text.components(separatedBy: "\n")
        var startIdx: Int? = nil
        for (i, line) in lines.enumerated() {
            if line.hasPrefix("**SCOUT") || line.hasPrefix("**Scout") {
                startIdx = i
                break
            }
            if line.hasPrefix("=== Run summary") {
                startIdx = i + 1
                break
            }
        }
        guard let start = startIdx else { return nil }
        var endIdx = lines.count
        for i in start..<lines.count {
            let l = lines[i]
            if l.hasPrefix("=== SCOUT") && l.contains("finished") {
                endIdx = i
                break
            }
        }
        let body = lines[start..<endIdx]
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? nil : body
    }

    /// The last lines of a log with no summary block, as plain text: the
    /// session's own markdown (`**Housekeeping:**`) showed raw in the
    /// monospaced box (UI audit T3), and a log tail isn't worth rendering.
    nonisolated static func fallbackPreview(of text: String) -> String {
        PlainText.strippingMarkdown(tail(text, lines: 20))
    }

    nonisolated static func tail(_ text: String, lines n: Int) -> String {
        let arr = text.components(separatedBy: "\n")
        return arr.suffix(n).joined(separator: "\n")
    }
}
