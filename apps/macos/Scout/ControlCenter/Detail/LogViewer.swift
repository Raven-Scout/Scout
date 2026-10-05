import SwiftUI
import AppKit

/// Pretty log viewer. Splits Scout's `=== marker ===` lines into section
/// headers, surfaces important banners (success / failure / budget /
/// timeout), and hides noisy boilerplate behind a "Raw" toggle.
struct LogViewer: View {
    let logPath: URL

    @State private var sections: [LogSection] = []
    @State private var rawText: String = ""
    @State private var search: String = ""
    @State private var showRaw: Bool = false
    @State private var watchTask: Task<Void, Never>? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            controls
            Divider()
            if showRaw {
                rawView
            } else {
                structuredView
            }
        }
        .task(id: logPath) { await reload(initial: true) }
        .onDisappear { watchTask?.cancel() }
    }

    // MARK: - Controls

    private var controls: some View {
        HStack(spacing: 8) {
            TextField("Search log…", text: $search)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 320)
            Spacer()
            EditorialSegmentedControl(
                selection: $showRaw,
                options: [("Pretty", false), ("Raw", true)],
                minSegmentWidth: 72
            )
            Button {
                NSWorkspace.shared.open(logPath)
            } label: {
                Label("Open", systemImage: "arrow.up.right.square")
            }
            .help("Open in default editor")
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(rawText, forType: .string)
            } label: {
                Label("Copy", systemImage: "doc.on.doc")
            }
            .help("Copy entire log")
        }
        .padding(.horizontal, 12)
        .padding(.top, 8)
    }

    // MARK: - Pretty / structured

    private var structuredView: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                ForEach(sections) { section in
                    sectionView(section)
                }
            }
            .padding(12)
        }
    }

    @ViewBuilder
    private func sectionView(_ section: LogSection) -> some View {
        let kind = section.kind
        let lines = filteredLines(section.lines)
        if lines.isEmpty && !search.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: kind.icon)
                        .foregroundStyle(kind.color)
                        .font(.caption)
                    Text(section.title)
                        .font(.system(.subheadline, design: .monospaced))
                        .fontWeight(.semibold)
                        .foregroundStyle(kind.color)
                    if let when = section.timestamp {
                        Text(when, format: .dateTime.hour().minute().second())
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }
                if !lines.isEmpty {
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                            lineView(line)
                        }
                    }
                    .padding(8)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color.secondary.opacity(0.06))
                    )
                }
            }
        }
    }

    @ViewBuilder
    private func lineView(_ line: String) -> some View {
        let highlighted = highlight(line: line)
        Text(highlighted)
            .font(.system(.callout, design: .monospaced))
            .textSelection(.enabled)
            .lineLimit(nil)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Coloring rules: keep the line monospaced (so users still recognize
    /// shell-style log lines) but tint the recognizable prefixes so eyes
    /// can find errors/budgets/timestamps fast.
    private func highlight(line: String) -> AttributedString {
        var attr = AttributedString(line)
        let lower = line.lowercased()
        if lower.contains("error") || lower.contains("rate limit")
            || lower.contains("timeout") || lower.contains("failed") {
            attr.foregroundColor = .red
        } else if lower.contains("budget") {
            attr.foregroundColor = .orange
        } else if lower.contains("[schema-parity] ok")
            || lower.contains("budget ok")
            || lower.contains("success") {
            attr.foregroundColor = .green
        } else if line.hasPrefix("**") || line.hasPrefix("# ") {
            attr.foregroundColor = .accentColor
        }
        return attr
    }

    private func filteredLines(_ lines: [String]) -> [String] {
        guard !search.isEmpty else { return lines }
        return lines.filter { $0.localizedCaseInsensitiveContains(search) }
    }

    // MARK: - Raw

    private var rawView: some View {
        ScrollView {
            Text(rawText.isEmpty ? "(empty log)" : rawText)
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .lineLimit(nil)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
        }
    }

    // MARK: - Loading

    private func reload(initial: Bool) async {
        watchTask?.cancel()
        let url = logPath
        let initialData = (try? Data(contentsOf: url)).flatMap { String(data: $0, encoding: .utf8) } ?? ""
        await applyText(initialData)

        // Tail subsequent writes so this view updates while the run is
        // still going. Cancellation comes from .onDisappear / view-id change.
        watchTask = Task { await self.tail(url: url) }
    }

    private func applyText(_ text: String) async {
        let parsed = LogParser.parse(text: text)
        await MainActor.run {
            self.rawText = text
            self.sections = parsed
        }
    }

    private func tail(url: URL) async {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return }
        defer { try? handle.close() }
        // Skip past what we already have.
        try? handle.seekToEnd()
        let fd = handle.fileDescriptor
        let src = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd, eventMask: .extend, queue: .global()
        )
        src.setEventHandler {
            // Re-read entire file on extend — it's small (<5MB) and parsing
            // is cheap; partial-append parsing isn't worth the bookkeeping.
            if let d = try? Data(contentsOf: url),
               let s = String(data: d, encoding: .utf8) {
                Task { await self.applyText(s) }
            }
        }
        src.resume()
        await withTaskCancellationHandler {
            try? await Task.sleep(nanoseconds: UInt64.max)
        } onCancel: {
            src.cancel()
        }
    }
}

// MARK: - Parser

struct LogSection: Identifiable {
    enum Kind {
        case start, finish, summary, budget, error, info

        var icon: String {
            switch self {
            case .start:   return "play.circle"
            case .finish:  return "checkmark.circle"
            case .summary: return "text.alignleft"
            case .budget:  return "dollarsign.circle"
            case .error:   return "exclamationmark.triangle"
            case .info:    return "circle"
            }
        }
        var color: Color {
            switch self {
            case .start:   return .blue
            case .finish:  return .green
            case .summary: return .accentColor
            case .budget:  return .orange
            case .error:   return .red
            case .info:    return .secondary
            }
        }
    }

    let id = UUID()
    let title: String
    let kind: Kind
    let timestamp: Date?
    let lines: [String]
}

enum LogParser {
    /// Walk the log line-by-line, splitting on `=== … ===` markers and
    /// `**SCOUT … complete**` headings. Anything between markers belongs to
    /// the previous section.
    static func parse(text: String) -> [LogSection] {
        var sections: [LogSection] = []
        var currentTitle: String = "Pre-run"
        var currentKind: LogSection.Kind = .info
        var currentLines: [String] = []
        var currentTs: Date? = nil

        func flush() {
            sections.append(LogSection(
                title: currentTitle,
                kind: currentKind,
                timestamp: currentTs,
                lines: currentLines
            ))
            currentLines = []
            currentTs = nil
        }

        for raw in text.components(separatedBy: "\n") {
            let line = raw
            if let parsed = parseMarker(line) {
                if !currentLines.isEmpty || currentTitle != "Pre-run" { flush() }
                currentTitle = parsed.title
                currentKind = parsed.kind
                currentTs = parsed.timestamp
                continue
            }
            if line.hasPrefix("**SCOUT") || line.hasPrefix("**Scout") {
                if !currentLines.isEmpty || currentTitle != "Pre-run" { flush() }
                currentTitle = "Run summary"
                currentKind = .summary
                currentTs = nil
                currentLines.append(line)
                continue
            }
            currentLines.append(line)
        }
        if !currentLines.isEmpty || sections.isEmpty {
            flush()
        }
        // Drop empty leading "Pre-run" section if it has nothing meaningful.
        if let first = sections.first,
           first.title == "Pre-run",
           first.lines.allSatisfy({ $0.trimmingCharacters(in: .whitespaces).isEmpty }) {
            sections.removeFirst()
        }
        return sections
    }

    private static func parseMarker(_ line: String) -> (title: String, kind: LogSection.Kind, timestamp: Date?)? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasPrefix("=== ") && trimmed.hasSuffix(" ===") else { return nil }
        let inner = String(trimmed.dropFirst(4).dropLast(4))
        let lower = inner.lowercased()
        let kind: LogSection.Kind
        if lower.contains("starting at") {
            kind = .start
        } else if lower.contains("finished at") {
            kind = .finish
        } else if lower.contains("budget") {
            kind = .budget
        } else if lower.contains("timeout") || lower.contains("error") || lower.contains("skipping") {
            kind = .error
        } else {
            kind = .info
        }
        // Try to extract a date if the marker has " at <date>"
        let date: Date? = {
            if let r = inner.range(of: " at ") {
                let tail = String(inner[r.upperBound...])
                let formats = [
                    "EEE MMM d HH:mm:ss zzz yyyy",
                    "EEE MMM  d HH:mm:ss zzz yyyy"
                ]
                for f in formats {
                    let df = DateFormatter()
                    df.locale = Locale(identifier: "en_US_POSIX")
                    df.dateFormat = f
                    if let d = df.date(from: tail) { return d }
                    // Sometimes the line ends with " (exit code: 0…)" so try
                    // again on the trimmed prefix before the parenthesis.
                    if let parenIdx = tail.firstIndex(of: "(") {
                        let head = String(tail[..<parenIdx]).trimmingCharacters(in: .whitespaces)
                        if let d = df.date(from: head) { return d }
                    }
                }
            }
            return nil
        }()
        // Strip the date so the title is short.
        let title: String = {
            if let r = inner.range(of: " at ") { return String(inner[..<r.lowerBound]) }
            return inner
        }()
        return (title, kind, date)
    }
}
