import Foundation

/// A structural block of a proposal body. Proposal bodies are free-form
/// markdown — bold-label paragraphs (`**Problem.** …`), prose, and fenced
/// code blocks (```bash … ```). Rendering them as one flat `Text` would mangle
/// the code, so the body is split into prose paragraphs (rendered with inline
/// markdown) and verbatim code blocks (rendered monospace).
nonisolated enum ProposalBodyBlock: Equatable, Sendable, Identifiable {
    /// A paragraph of prose; may contain inline markdown (bold, links,
    /// `[[wikilinks]]`). Rendered via `InlineMarkdownText`.
    case prose(String)
    /// A fenced code block, rendered verbatim in a monospace panel.
    case code(language: String?, code: String)

    var id: String {
        switch self {
        case .prose(let t):          return "p:\(t)"
        case .code(let lang, let c): return "c:\(lang ?? ""):\(c)"
        }
    }

    /// Split a raw proposal body into ordered blocks. Fenced code blocks
    /// (lines bounded by ```` ``` ````) are lifted out verbatim; the prose
    /// between them is broken into paragraphs on blank lines.
    static func blocks(from rawBody: String) -> [ProposalBodyBlock] {
        let lines = rawBody.components(separatedBy: "\n")
        var blocks: [ProposalBodyBlock] = []
        var proseBuffer: [String] = []
        var codeBuffer: [String] = []
        var codeLanguage: String?
        var inCode = false

        func flushProse() {
            let joined = proseBuffer.joined(separator: "\n")
            for para in paragraphs(in: joined) {
                blocks.append(.prose(para))
            }
            proseBuffer.removeAll(keepingCapacity: true)
        }

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inCode {
                    // Closing fence — emit the accumulated code.
                    blocks.append(.code(language: codeLanguage,
                                        code: codeBuffer.joined(separator: "\n")))
                    codeBuffer.removeAll(keepingCapacity: true)
                    codeLanguage = nil
                    inCode = false
                } else {
                    // Opening fence — flush any pending prose first.
                    flushProse()
                    let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    codeLanguage = lang.isEmpty ? nil : lang
                    inCode = true
                }
                continue
            }
            if inCode {
                codeBuffer.append(line)
            } else {
                proseBuffer.append(line)
            }
        }

        // Unterminated fence (malformed markdown): keep its content as code so
        // nothing is dropped.
        if inCode {
            blocks.append(.code(language: codeLanguage, code: codeBuffer.joined(separator: "\n")))
        }
        flushProse()
        return blocks
    }

    /// Split a block of text into paragraphs on runs of blank lines, trimming
    /// surrounding whitespace and dropping empties.
    private static func paragraphs(in text: String) -> [String] {
        text
            .components(separatedBy: "\n")
            .reduce(into: [[String]]()) { acc, line in
                if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    if acc.last?.isEmpty == false { acc.append([]) }
                } else {
                    if acc.isEmpty { acc.append([]) }
                    acc[acc.count - 1].append(line)
                }
            }
            .map { $0.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}
