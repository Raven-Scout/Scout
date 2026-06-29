import SwiftUI
import AppKit

/// A "live preview" markdown editor (Obsidian-style): the text you edit is exact
/// markdown — nothing is converted or round-tripped — but it's styled inline as
/// you type (headings enlarged, **bold**/_italic_ rendered, `code`, `[[wikilinks]]`,
/// links, tags, tables and code fences highlighted, syntax markers dimmed).
///
/// Because the underlying string is never transformed, the KB's load-bearing
/// tokens (`[[wikilinks]]`, `[#TAG]`, tables, frontmatter) are preserved exactly
/// for the scout-plugin — unlike a true WYSIWYG model that re-serializes.
struct KBLiveEditor: NSViewRepresentable {
    @Binding var text: String

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        scroll.borderType = .noBorder
        scroll.drawsBackground = true
        scroll.backgroundColor = NSColor(DS.Paper.sunk)
        guard let tv = scroll.documentView as? NSTextView else { return scroll }

        tv.delegate = context.coordinator
        tv.isRichText = false
        tv.allowsUndo = true
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.isAutomaticTextReplacementEnabled = false
        tv.textContainerInset = NSSize(width: 16, height: 14)
        tv.backgroundColor = NSColor(DS.Paper.sunk)
        tv.drawsBackground = true
        tv.typingAttributes = Coordinator.baseAttributes
        tv.string = text
        context.coordinator.textView = tv
        context.coordinator.highlight()
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let tv = scroll.documentView as? NSTextView else { return }
        if tv.string != text {
            context.coordinator.isProgrammatic = true
            let sel = tv.selectedRange()
            tv.string = text
            let len = (text as NSString).length
            tv.setSelectedRange(NSRange(location: min(sel.location, len), length: 0))
            context.coordinator.highlight()
            context.coordinator.isProgrammatic = false
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: KBLiveEditor
        weak var textView: NSTextView?
        var isProgrammatic = false
        private var restyleItem: DispatchWorkItem?

        init(_ parent: KBLiveEditor) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard let tv = textView, !isProgrammatic else { return }
            parent.text = tv.string
            scheduleHighlight()
        }

        /// Debounce restyling so typing in a large note stays smooth.
        private func scheduleHighlight() {
            restyleItem?.cancel()
            let item = DispatchWorkItem { [weak self] in self?.highlight() }
            restyleItem = item
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.12, execute: item)
        }

        // MARK: - Fonts & colors

        static let bodyFont = serif(14)
        static let baseAttributes: [NSAttributedString.Key: Any] = [
            .font: bodyFont, .foregroundColor: NSColor(DS.Ink.p1),
        ]

        static func serif(_ size: CGFloat) -> NSFont {
            NSFont(name: "Newsreader", size: size)
                ?? NSFont(name: "New York", size: size)
                ?? NSFont.systemFont(ofSize: size)
        }
        static func mono(_ size: CGFloat) -> NSFont {
            NSFont(name: "JetBrains Mono", size: size)
                ?? NSFont.monospacedSystemFont(ofSize: size, weight: .regular)
        }
        static func bold(_ font: NSFont) -> NSFont {
            NSFontManager.shared.convert(font, toHaveTrait: .boldFontMask)
        }
        static func italic(_ font: NSFont) -> NSFont {
            NSFontManager.shared.convert(font, toHaveTrait: .italicFontMask)
        }
        static func headingSize(_ level: Int) -> CGFloat {
            switch level { case 1: return 23; case 2: return 19; case 3: return 16.5; default: return 14.5 }
        }

        private var faint: NSColor { NSColor(DS.Ink.p4) }
        private var accent: NSColor { NSColor(DS.Accent.ink) }

        // MARK: - Highlighting

        func highlight() {
            guard let tv = textView, let storage = tv.textStorage else { return }
            let ns = tv.string as NSString
            let full = NSRange(location: 0, length: ns.length)
            storage.beginEditing()
            storage.setAttributes(Self.baseAttributes, range: full)

            // Block-level
            forEach(#"^---\n[\s\S]*?\n---"#, in: ns) { m in     // frontmatter
                self.setFont(Self.mono(11), m.range, in: storage)
                storage.addAttribute(.foregroundColor, value: self.faint, range: m.range)
            }
            forEach(#"```[\s\S]*?```"#, in: ns) { m in           // fenced code
                self.setFont(Self.mono(12), m.range, in: storage)
                storage.addAttribute(.foregroundColor, value: NSColor(DS.Ink.p2), range: m.range)
            }
            forEach(#"^\s*\|.*\|\s*$"#, options: [.anchorsMatchLines], in: ns) { m in  // table rows
                self.setFont(Self.mono(11.5), m.range, in: storage)
            }

            // Inline
            forEach(#"\*\*([^*\n]+)\*\*"#, in: ns) { m in        // bold
                self.setFont(Self.bold(Self.bodyFont), m.range, in: storage)
                self.dimMarkers(m.range, markerLen: 2, in: storage)
            }
            forEach(#"(?<!\w)_([^_\n]+)_(?!\w)"#, in: ns) { m in  // italic
                self.setFont(Self.italic(Self.bodyFont), m.range, in: storage)
            }
            forEach(#"`([^`\n]+)`"#, in: ns) { m in              // inline code
                self.setFont(Self.mono(12.5), m.range, in: storage)
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range)
            }
            forEach(#"\[([^\]\n]+)\]\(([^)\n]+)\)"#, in: ns) { m in  // [text](url)
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range(at: 1))
                storage.addAttribute(.foregroundColor, value: self.faint, range: m.range(at: 2))
            }
            forEach(#"\[\[([^\]\n]+)\]\]"#, in: ns) { m in       // [[wikilink]]
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range)
                self.dimMarkers(m.range, markerLen: 2, in: storage)
            }
            forEach(#"\[#[^\]\n]+\]|(?<![\w/])#[A-Za-z][\w-]+"#, in: ns) { m in  // [#TAG] / #tag
                storage.addAttribute(.foregroundColor, value: NSColor(DS.SlotType.consolidation), range: m.range)
            }
            forEach(#"^\s*>\s?.*$"#, options: [.anchorsMatchLines], in: ns) { m in  // blockquote
                storage.addAttribute(.foregroundColor, value: NSColor(DS.Ink.p3), range: m.range)
            }
            forEach(#"^(\s*)([-*+]|\d+\.)\s"#, options: [.anchorsMatchLines], in: ns) { m in  // list markers
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range(at: 2))
            }

            // Headings last so the line font wins over any inline styling.
            forEach(#"^(#{1,6})\s+.*$"#, options: [.anchorsMatchLines], in: ns) { m in
                let level = m.range(at: 1).length
                self.setFont(Self.bold(Self.serif(Self.headingSize(level))), m.range, in: storage)
                storage.addAttribute(.foregroundColor, value: NSColor(DS.Ink.p1), range: m.range)
                storage.addAttribute(.foregroundColor, value: self.faint, range: m.range(at: 1))  // dim ###
            }

            storage.endEditing()
        }

        private func setFont(_ font: NSFont, _ range: NSRange, in storage: NSTextStorage) {
            storage.addAttribute(.font, value: font, range: range)
        }

        /// Dim the leading and trailing `markerLen` characters of a delimited
        /// span (e.g. the `**` or `[[`/`]]`) so syntax recedes but stays visible.
        private func dimMarkers(_ range: NSRange, markerLen: Int, in storage: NSTextStorage) {
            guard range.length >= markerLen * 2 else { return }
            storage.addAttribute(.foregroundColor, value: faint,
                                 range: NSRange(location: range.location, length: markerLen))
            storage.addAttribute(.foregroundColor, value: faint,
                                 range: NSRange(location: range.location + range.length - markerLen, length: markerLen))
        }

        private func forEach(_ pattern: String,
                             options: NSRegularExpression.Options = [],
                             in text: NSString,
                             _ body: (NSTextCheckingResult) -> Void) {
            guard let re = try? NSRegularExpression(pattern: pattern, options: options) else { return }
            re.enumerateMatches(in: text as String, range: NSRange(location: 0, length: text.length)) { m, _, _ in
                if let m { body(m) }
            }
        }
    }
}
