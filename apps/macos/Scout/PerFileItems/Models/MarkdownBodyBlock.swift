import Foundation

/// A structural block of a per-file item body: prose paragraphs and fenced code.
nonisolated enum MarkdownBodyBlock: Equatable, Sendable, Identifiable {
    case prose(String)
    case code(language: String?, code: String)

    var id: String {
        switch self {
        case .prose(let t):          return "p:\(t)"
        case .code(let lang, let c): return "c:\(lang ?? ""):\(c)"
        }
    }

    static func blocks(from rawBody: String) -> [MarkdownBodyBlock] {
        let lines = rawBody.components(separatedBy: "\n")
        var blocks: [MarkdownBodyBlock] = []
        var proseBuffer: [String] = []
        var codeBuffer: [String] = []
        var codeLanguage: String?
        var inCode = false

        func flushProse() {
            let joined = proseBuffer.joined(separator: "\n")
            for para in paragraphs(in: joined) { blocks.append(.prose(para)) }
            proseBuffer.removeAll(keepingCapacity: true)
        }

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inCode {
                    blocks.append(.code(language: codeLanguage, code: codeBuffer.joined(separator: "\n")))
                    codeBuffer.removeAll(keepingCapacity: true)
                    codeLanguage = nil
                    inCode = false
                } else {
                    flushProse()
                    let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    codeLanguage = lang.isEmpty ? nil : lang
                    inCode = true
                }
                continue
            }
            if inCode { codeBuffer.append(line) } else { proseBuffer.append(line) }
        }

        if inCode { blocks.append(.code(language: codeLanguage, code: codeBuffer.joined(separator: "\n"))) }
        flushProse()
        return blocks
    }

    private static func paragraphs(in text: String) -> [String] {
        text.components(separatedBy: "\n")
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
