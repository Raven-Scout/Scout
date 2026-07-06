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
///
/// Styling is windowed: only the visible range (plus a screenful of slop,
/// expanded to cover any code fence / frontmatter block it touches) is restyled
/// per keystroke or scroll, so a large hub note doesn't pay ~a dozen
/// full-document regex passes on every debounce. Fence/frontmatter boundaries
/// come from a single cheap line scan of the whole document, so block context
/// stays correct no matter where the window sits.
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

        // Restyle newly revealed text when the user scrolls.
        scroll.contentView.postsBoundsChangedNotifications = true
        NotificationCenter.default.addObserver(
            context.coordinator, selector: #selector(Coordinator.didScroll),
            name: NSView.boundsDidChangeNotification, object: scroll.contentView)

        context.coordinator.highlight()
        return scroll
    }

    static func dismantleNSView(_ scroll: NSScrollView, coordinator: Coordinator) {
        // Selector observers auto-unregister on dealloc (macOS ≥ 10.11), but
        // remove promptly so a lingering coordinator stops restyle work as soon
        // as the editor goes away (mode switch, note change).
        NotificationCenter.default.removeObserver(coordinator)
        coordinator.cancelPendingRestyle()
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
        /// Fence/frontmatter ranges styled by the previous pass — a window that
        /// touches where a block *used* to be must restyle the whole old extent,
        /// or deleting a fence delimiter would leave stale styling below.
        private var lastBlockRanges: [NSRange] = []

        init(_ parent: KBLiveEditor) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard let tv = textView, !isProgrammatic else { return }
            parent.text = tv.string
            scheduleHighlight()
        }

        @objc func didScroll(_ note: Notification) {
            scheduleHighlight()
        }

        func cancelPendingRestyle() {
            restyleItem?.cancel()
            restyleItem = nil
        }

        /// Debounce restyling so typing/scrolling in a large note stays smooth.
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

        private var faint: NSColor { NSColor(DS.Ink.p4) }
        private var accent: NSColor { NSColor(DS.Accent.ink) }

        // MARK: - Block map (single line scan, no regex)

        /// Character ranges of the leading `--- … ---` frontmatter and every
        /// ``` fenced code block (an unterminated fence runs to the end).
        private struct BlockMap {
            var frontmatter: NSRange?
            var fences: [NSRange] = []
            var all: [NSRange] { fences + (frontmatter.map { [$0] } ?? []) }
        }

        private func computeBlockMap(_ ns: NSString) -> BlockMap {
            var map = BlockMap()
            let len = ns.length
            var pos = 0
            var isFirstLine = true
            var frontmatterStart: Int? = nil
            var fenceStart: Int? = nil
            while pos < len {
                let lineRange = ns.lineRange(for: NSRange(location: pos, length: 0))
                let line = ns.substring(with: lineRange)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if isFirstLine, line == "---" {
                    frontmatterStart = lineRange.location
                } else if let fm = frontmatterStart, map.frontmatter == nil, line == "---" {
                    map.frontmatter = NSRange(location: fm, length: NSMaxRange(lineRange) - fm)
                } else if frontmatterStart == nil || map.frontmatter != nil {
                    if line.hasPrefix("```") {
                        if let start = fenceStart {
                            map.fences.append(NSRange(location: start, length: NSMaxRange(lineRange) - start))
                            fenceStart = nil
                        } else {
                            fenceStart = lineRange.location
                        }
                    }
                }
                isFirstLine = false
                pos = NSMaxRange(lineRange)
            }
            if let start = fenceStart {
                map.fences.append(NSRange(location: start, length: len - start))
            }
            // An unterminated frontmatter fence is plain text (matches read mode).
            return map
        }

        // MARK: - Highlighting

        func highlight() {
            guard let tv = textView, let storage = tv.textStorage else { return }
            let ns = tv.string as NSString
            let len = ns.length
            guard len > 0 else { return }

            let map = computeBlockMap(ns)

            // Window: the visible text plus a screenful of slop (fallback: the
            // document head before the first layout pass), expanded to whole
            // lines and to fully cover any current or previous block it touches.
            var window = visibleCharRange(tv)
            if window.length == 0 { window = NSRange(location: 0, length: min(len, 20_000)) }
            let full = NSRange(location: 0, length: len)
            var blocks = map.all
            blocks += lastBlockRanges.map { NSIntersectionRange($0, full) }.filter { $0.length > 0 }
            var expanded = window
            var grew = true
            while grew {
                grew = false
                for b in blocks {
                    if NSIntersectionRange(expanded, b).length > 0, NSUnionRange(expanded, b) != expanded {
                        expanded = NSUnionRange(expanded, b); grew = true
                    }
                }
            }
            expanded = ns.lineRange(for: expanded)
            lastBlockRanges = map.all

            storage.beginEditing()
            storage.setAttributes(Self.baseAttributes, range: expanded)

            // Block-level (from the map — no unbounded multi-line regexes).
            if let fm = map.frontmatter, NSIntersectionRange(fm, expanded).length > 0 {
                setFont(Self.mono(11), fm, in: storage)
                storage.addAttribute(.foregroundColor, value: faint, range: fm)
            }
            for fence in map.fences where NSIntersectionRange(fence, expanded).length > 0 {
                setFont(Self.mono(12), fence, in: storage)
                storage.addAttribute(.foregroundColor, value: NSColor(DS.Ink.p2), range: fence)
            }
            forEach(#"^\s*\|.*\|\s*$"#, options: [.anchorsMatchLines], in: ns, range: expanded) { m in  // table rows
                self.setFont(Self.mono(11.5), m.range, in: storage)
            }

            // Inline (all line-scoped patterns).
            forEach(#"\*\*([^*\n]+)\*\*"#, in: ns, range: expanded) { m in        // bold
                self.setFont(Self.bold(Self.bodyFont), m.range, in: storage)
                self.dimMarkers(m.range, markerLen: 2, in: storage)
            }
            forEach(#"(?<!\w)_([^_\n]+)_(?!\w)"#, in: ns, range: expanded) { m in  // italic
                self.setFont(Self.italic(Self.bodyFont), m.range, in: storage)
            }
            forEach(#"`([^`\n]+)`"#, in: ns, range: expanded) { m in              // inline code
                self.setFont(Self.mono(12.5), m.range, in: storage)
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range)
            }
            forEach(#"\[([^\]\n]+)\]\(([^)\n]+)\)"#, in: ns, range: expanded) { m in  // [text](url)
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range(at: 1))
                storage.addAttribute(.foregroundColor, value: self.faint, range: m.range(at: 2))
            }
            forEach(#"\[\[([^\]\n]+)\]\]"#, in: ns, range: expanded) { m in       // [[wikilink]]
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range)
                self.dimMarkers(m.range, markerLen: 2, in: storage)
            }
            forEach(#"\[#[^\]\n]+\]|(?<![\w/])#[A-Za-z][\w-]+"#, in: ns, range: expanded) { m in  // [#TAG] / #tag
                storage.addAttribute(.foregroundColor, value: NSColor(DS.SlotType.consolidation), range: m.range)
            }
            forEach(#"^\s*>\s?.*$"#, options: [.anchorsMatchLines], in: ns, range: expanded) { m in  // blockquote
                storage.addAttribute(.foregroundColor, value: NSColor(DS.Ink.p3), range: m.range)
            }
            forEach(#"^(\s*)([-*+]|\d+\.)\s"#, options: [.anchorsMatchLines], in: ns, range: expanded) { m in  // list markers
                storage.addAttribute(.foregroundColor, value: self.accent, range: m.range(at: 2))
            }

            // Headings last so the line font wins over any inline styling.
            forEach(#"^(#{1,6})\s+.*$"#, options: [.anchorsMatchLines], in: ns, range: expanded) { m in
                let level = m.range(at: 1).length
                self.setFont(Self.bold(Self.serif(KBMarkdownLexer.headingSize(level))), m.range, in: storage)
                storage.addAttribute(.foregroundColor, value: NSColor(DS.Ink.p1), range: m.range)
                storage.addAttribute(.foregroundColor, value: self.faint, range: m.range(at: 1))  // dim ###
            }

            storage.endEditing()
        }

        /// Character range currently on screen, padded by one viewport height
        /// above and below so small scrolls stay styled.
        private func visibleCharRange(_ tv: NSTextView) -> NSRange {
            guard let lm = tv.layoutManager, let tc = tv.textContainer else {
                return NSRange(location: 0, length: 0)
            }
            var rect = tv.visibleRect
            guard rect.height > 0 else { return NSRange(location: 0, length: 0) }
            rect = rect.insetBy(dx: 0, dy: -rect.height)
            let glyphs = lm.glyphRange(forBoundingRect: rect, in: tc)
            return lm.characterRange(forGlyphRange: glyphs, actualGlyphRange: nil)
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
                             range: NSRange,
                             _ body: (NSTextCheckingResult) -> Void) {
            guard let re = try? NSRegularExpression(pattern: pattern, options: options) else { return }
            re.enumerateMatches(in: text as String, range: range) { m, _, _ in
                if let m { body(m) }
            }
        }
    }
}
