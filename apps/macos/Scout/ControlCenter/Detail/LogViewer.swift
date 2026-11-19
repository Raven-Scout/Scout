import SwiftUI
import AppKit

/// Pretty log viewer. Splits Scout's `=== marker ===` lines into section
/// headers, surfaces important banners (success / failure / budget /
/// timeout), and hides noisy boilerplate behind a "Raw" toggle.
struct LogViewer: View {
    let logPath: URL

    @State private var sections: [LogSection] = []
    @State private var rawText: String = ""
    /// `rawText` split by `TextChunks`, kept beside it so a render never re-splits.
    @State private var rawChunks: [String] = []
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

    /// Each section is a header row plus its lines in chunks of
    /// `TextChunks.linesPerChunk`, every one a row of the lazy stack, so a
    /// long section is laid out only as it scrolls into view instead of as
    /// one view thousands of lines tall (UI audit R10, #25).
    private var structuredView: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(sections) { section in
                    // One line past the chunk byte cap (a minified blob) is
                    // cut here; Raw and Copy still have all of it.
                    let lines = filteredLines(section.lines).map { TextChunks.clipped($0) }
                    if !(lines.isEmpty && !search.isEmpty) {
                        sectionHeader(section)
                            .padding(.top, 10)
                            .padding(.bottom, 6)
                        let chunks = TextChunks.split(lines)
                        ForEach(Array(chunks.enumerated()), id: \.offset) { i, chunk in
                            chunkView(chunk, first: i == 0, last: i == chunks.count - 1)
                        }
                    }
                }
            }
            .padding(12)
        }
    }

    private func sectionHeader(_ section: LogSection) -> some View {
        HStack(spacing: 8) {
            Image(systemName: section.kind.icon)
                .foregroundStyle(section.kind.color)
                .font(DS.sans(10))
                .accessibilityHidden(true)
            Text(section.title)
                .font(DS.mono(11))
                .fontWeight(.semibold)
                .foregroundStyle(section.kind.color)
            if let when = section.timestamp {
                Text(when, format: .dateTime.hour().minute().second())
                    .font(DS.mono(10))
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// A run of lines as one `Text`, each line tinted as `highlight` says.
    private func chunkView(_ lines: [String], first: Bool, last: Bool) -> some View {
        var text = AttributedString()
        for (i, line) in lines.enumerated() {
            if i > 0 { text.append(AttributedString("\n")) }
            text.append(highlight(line: line))
        }
        return Text(text)
            .font(DS.mono(12))
            .lineSpacing(1)
            .textSelection(.enabled)
            .lineLimit(nil)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 8)
            .padding(.top, first ? 8 : 0)
            .padding(.bottom, last ? 8 : 0)
            .background(Rectangle().fill(Color.secondary.opacity(0.06)))
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

    /// The raw log in chunks, one lazy row each (UI audit R10, #25). Copy
    /// still copies the whole file.
    private var rawView: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                if rawChunks.isEmpty {
                    Text("(empty log)").font(DS.mono(12)).foregroundStyle(.secondary)
                }
                ForEach(Array(rawChunks.enumerated()), id: \.offset) { _, chunk in
                    Text(chunk)
                        .font(DS.mono(12))
                        .textSelection(.enabled)
                        .lineLimit(nil)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
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
        let chunks = TextChunks.split(text: text)
        await MainActor.run {
            self.rawText = text
            self.rawChunks = chunks
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

// MARK: - Chunking

/// Splits long text into runs of lines, so a big log or diff renders as many
/// small `Text` views in a lazy stack or list (laid out as they scroll in)
/// rather than one `Text` that lays out every line up front (UI audit R10, #25).
nonisolated enum TextChunks {
    static let linesPerChunk = 200
    /// A chunk also ends once it holds this many UTF-8 bytes, so a log of
    /// very long lines (minified JSON, a base64 blob) still comes in pieces.
    static let bytesPerChunk = 64 * 1024

    /// `lines` in order: a chunk ends at `size` lines or once it reaches
    /// `maxBytes`. A single line longer than that is a chunk of its own.
    /// Empty for no lines.
    static func split(_ lines: [String], size: Int = linesPerChunk,
                      maxBytes: Int = bytesPerChunk) -> [[String]] {
        let size = max(1, size)
        var chunks: [[String]] = []
        var current: [String] = []
        var bytes = 0
        for line in lines {
            let n = line.utf8.count + 1
            if !current.isEmpty && (current.count >= size || bytes + n > maxBytes) {
                chunks.append(current)
                current = []
                bytes = 0
            }
            current.append(line)
            bytes += n
        }
        if !current.isEmpty { chunks.append(current) }
        return chunks
    }

    /// `line` cut to at most `maxBytes` UTF-8 bytes, at a whole character,
    /// with a note saying how much was left out; a shorter line unchanged.
    /// The pretty log view shows one line as one run of text, and a single
    /// line of megabytes would still be laid out whole.
    static func clipped(_ line: String, maxBytes: Int = bytesPerChunk) -> String {
        let total = line.utf8.count
        guard total > maxBytes else { return line }
        var end = line.utf8.index(line.utf8.startIndex, offsetBy: max(0, maxBytes))
        while end.samePosition(in: line) == nil { end = line.utf8.index(before: end) }
        let kept = line[..<end]
        let omitted = ByteCountFormatter.string(fromByteCount: Int64(total - kept.utf8.count), countStyle: .file)
        return String(kept) + " … (line cut: \(omitted) more; Raw shows all of it)"
    }

    /// `text`'s lines in chunks (`split(_:size:maxBytes:)`), each joined back
    /// with "\n"; empty for empty text. Any line ending counts: "\r\n" is one
    /// `Character` in Swift, so splitting on "\n" alone would leave a CRLF log
    /// as one line.
    static func split(text: String, size: Int = linesPerChunk, maxBytes: Int = bytesPerChunk) -> [String] {
        guard !text.isEmpty else { return [] }
        let lines = text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline).map(String.init)
        return split(lines, size: size, maxBytes: maxBytes).map { $0.joined(separator: "\n") }
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
