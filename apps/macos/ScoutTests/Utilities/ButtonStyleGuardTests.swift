import Foundation
import Testing

/// Design-system v2 (#105): actions use the DS button styles (`.dsPrimary`, `.dsQuiet`,
/// `.dsSecondary`, `.dsLink`, `.dsDestructive`), so every button draws the
/// same disabled, pressed and focus states. This scans the app's sources for
/// the two things that drift from that: the system bordered styles, and an
/// accent button hand-built from `.plainHit`/`.plain` plus accent colours.
///
/// To fix a hit, pick the DS role (see `Utilities/DSButtonStyle.swift`) and
/// drop the caller's `.font`, `.foregroundStyle`, padding and background:
/// the style draws them.
@Suite("Buttons use the design-system styles")
struct ButtonStyleGuardTests {

    /// Files allowed a system or hand-built style, and why.
    static let allowlist: [String: String] = [
        // A toolbar control: the window toolbar draws its own bezel.
        "ActionItems/Views/DatePickerToolbarItem.swift": "toolbar",
        // The status-bar health cell: the whole cell is the hit area, and its
        // accent text is a "fix" hint inside it, not a button label.
        "Shell/StatusBarView.swift": "hit area",
    ]

    /// A `.plainHit`/`.plain` line carrying this marker is a hit area (an
    /// icon button, a chip whose icon is accent), not an action button. Say
    /// why after it, as the existing ones do.
    static let hitAreaMarker = "ds-guard: hit area"

    static let systemStyle = try! NSRegularExpression(
        pattern: #"\.buttonStyle\(\.(bordered|borderedProminent|link|borderless)\)"#)
    static let plainStyle = try! NSRegularExpression(pattern: #"\.buttonStyle\(\.(plainHit|plain)\)"#)
    /// Accent colours on a plain button make it a hand-built primary or link.
    static let accentColour = try! NSRegularExpression(
        pattern: #"foregroundStyle\(DS\.Accent\.(ink|onFill)\)|\.fill\(DS\.Accent\.fill\)|background\(DS\.Accent\.fill"#)

    static var sourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Scout")
    }

    /// Every app source file, relative path → lines, minus the allowlist.
    static func sources() throws -> [(String, [String])] {
        let root = sourceRoot
        let enumerator = try #require(FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil))
        var out: [(String, [String])] = []
        for case let url as URL in enumerator where url.pathExtension == "swift" {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            guard allowlist[relative] == nil else { continue }
            out.append((relative, try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")))
        }
        return out
    }

    static func matches(_ regex: NSRegularExpression, _ line: String) -> Bool {
        regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
    }

    static func isComment(_ line: String) -> Bool {
        line.trimmingCharacters(in: .whitespaces).hasPrefix("//")
    }

    @Test("no system bordered or link button styles")
    func noSystemStyles() throws {
        let files = try Self.sources()
        #expect(files.count > 100, "found only \(files.count) Swift files")
        // `.borderless` stays allowed for icon-only buttons (the menu-bar
        // footer); only the text-button styles are flagged.
        var hits: [String] = []
        for (file, lines) in files {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.systemStyle, line) {
                if line.contains(".borderless") { continue }
                hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
            }
        }
        #expect(hits.isEmpty, "use .dsPrimary/.dsSecondary/.dsLink instead:\n\(hits.joined(separator: "\n"))")
    }

    @Test("no accent buttons hand-built from .plainHit or .plain")
    func noHandBuiltAccentButtons() throws {
        var hits: [String] = []
        for (file, lines) in try Self.sources() {
            for (i, line) in lines.enumerated()
            where !Self.isComment(line) && Self.matches(Self.plainStyle, line) && !line.contains(Self.hitAreaMarker) {
                // The button's own modifier chain: its label above, and the
                // modifiers that follow `.buttonStyle` below.
                let window = lines[max(0, i - 8)...min(lines.count - 1, i + 3)].filter { !Self.isComment($0) }
                if window.contains(where: { Self.matches(Self.accentColour, $0) }) {
                    hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        #expect(hits.isEmpty, "a hand-built accent button — use .dsPrimary or .dsLink:\n\(hits.joined(separator: "\n"))")
    }


    /// A grey label on a plain button: a hand-built quiet action.
    static let greyColour = try! NSRegularExpression(
        pattern: #"foregroundStyle\((DS\.Ink\.p[234]|\.secondary|\.tertiary)\)"#)
    /// A button with a text title (not an icon-only hit area).
    static let titledButton = try! NSRegularExpression(pattern: #"Button\("|Label\(""#)

    @Test("no quiet text actions hand-built from .plainHit or .plain")
    func noHandBuiltQuietButtons() throws {
        var hits: [String] = []
        for (file, lines) in try Self.sources() {
            for (i, line) in lines.enumerated()
            where !Self.isComment(line) && Self.matches(Self.plainStyle, line) && !line.contains(Self.hitAreaMarker) {
                // The button itself: from the nearest `Button` above the
                // style line down to it, then the modifiers that follow.
                guard let start = (max(0, i - 8)...i).reversed().first(where: { lines[$0].contains("Button") })
                else { continue }
                let declaration = lines[start...i].filter { !Self.isComment($0) }
                let modifiers = lines[i...min(lines.count - 1, i + 3)].filter { !Self.isComment($0) }
                if declaration.contains(where: { Self.matches(Self.titledButton, $0) })
                    && (declaration + modifiers).contains(where: { Self.matches(Self.greyColour, $0) }) {
                    hits.append("\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))")
                }
            }
        }
        #expect(hits.isEmpty, """
            a grey text button built by hand — use .dsQuiet (or .ds(.quiet, size: .small)) and drop its \
            .font and .foregroundStyle; mark a real hit area "// ds-guard: hit area":
            \(hits.joined(separator: "\n"))
            """)
    }

    /// Files whose buttons are menu items, menu-bar commands or alert
    /// buttons, which stay system buttons (#688: "Alerts and confirmation
    /// dialogs should stay system buttons").
    static let systemButtonFiles: [String: String] = [
        "AppDelegate.swift": "NSAlert",
        "Shell/GoCommands.swift": "menu commands",
        "Shell/CheckForUpdatesView.swift": "a menu item",
        "Utilities/LaunchFailureAlert.swift": "an alert",
        "Utilities/PlainLanguage/Glossary.swift": "the Help menu item",
        "PerFileItems/Views/ItemPriorityPill.swift": "the priority menu's items",
        "Help/HelpCommands.swift": "Help menu commands",
    ]

    static let swiftUIButton = try! NSRegularExpression(pattern: #"\bButton\s*[\(\{]"#)
    /// `Button("…")` or `Button(title)`: a button with a text title.
    static let titledStart = try! NSRegularExpression(pattern: #"\bButton\s*\(\s*(?!action:|role:)[^)\{]"#)
    /// Blocks whose buttons stay system buttons: menus, alert and dialog
    /// actions, toolbars, and hidden shortcut carriers in a `.background`.
    /// A helper that builds menu items says so with `// ds-guard: menu items`.
    static let systemContext = try! NSRegularExpression(
        pattern: #"\bMenu\b|\.alert\(|confirmationDialog\(|contextMenu|[mM]enuItems|CommandMenu|CommandGroup|ds-guard: menu|\.toolbar\b|ToolbarItem|\.background\s*\{"#)

    static func indent(_ s: String) -> Int { s.prefix(while: { $0 == " " }).count }

    /// The blocks around line `i`, innermost first, as (opening line,
    /// closing line).
    static func enclosingBlocks(_ lines: [String], _ i: Int, levels: Int) -> [(Int, Int)] {
        var openers: [Int] = []
        var depth = 0
        var j = i - 1
        while j >= max(0, i - 80) && openers.count < levels {
            for ch in lines[j].reversed() {
                if ch == "}" { depth += 1 } else if ch == "{" { if depth == 0 { openers.append(j) } else { depth -= 1 } }
            }
            j -= 1
        }
        return openers.compactMap { o in
            var d = 0
            let brace = lines[o].lastIndex(of: "{") ?? lines[o].startIndex
            for k in o..<min(lines.count, o + 200) {
                let text = k == o ? String(lines[o][brace...]) : lines[k]
                for ch in text { if ch == "{" { d += 1 } else if ch == "}" { d -= 1 } }
                if d <= 0 && k > o { return (o, k) }
            }
            return nil
        }
    }

    /// The `.buttonStyle` line that styles the button starting at line `i`:
    /// in its own modifier chain, or on one of the two containers around it.
    /// "system" when the button sits in a menu, alert, dialog or toolbar.
    static func governingStyle(_ lines: [String], _ i: Int) -> String? {
        for (o, _) in enclosingBlocks(lines, i, levels: 3) {
            let context = lines[max(0, o - 5)...o].joined(separator: "\n")
            if matches(systemContext, context) { return "system" }
        }
        if lines[i].contains(".buttonStyle(") { return lines[i] }
        let ind = indent(lines[i])
        for j in (i + 1)..<min(lines.count, i + 40) {
            let t = lines[j].trimmingCharacters(in: .whitespaces)
            if t.isEmpty || t.hasPrefix("//") { continue }
            let jind = indent(lines[j])
            if jind <= ind && !(t.hasPrefix(".") || t.hasPrefix("}")) { break }
            if lines[j].contains(".buttonStyle(")
                && ((jind == ind && (t.hasPrefix(".") || t.hasPrefix("}"))) || (jind == ind + 4 && t.hasPrefix("."))) {
                return lines[j]
            }
        }
        for (_, close) in enclosingBlocks(lines, i, levels: 2) {
            if let hit = lines[close..<min(lines.count, close + 4)].first(where: { $0.contains(".buttonStyle(") }) {
                return hit
            }
        }
        return nil
    }

    /// Every button gets a role: no system push buttons outside menus and
    /// alerts, and no titled action on `.plainHit`/`.plain`, which reads as
    /// plain text, unless it's marked as a hit area. Checked per button, so
    /// one styled button doesn't excuse the rest of its file.
    @Test("every button outside menus and alerts has a DS role")
    func everyButtonHasARole() throws {
        var unstyled: [String] = []
        var plainTitled: [String] = []
        for (file, lines) in try Self.sources() where Self.systemButtonFiles[file] == nil {
            for (i, line) in lines.enumerated() where !Self.isComment(line) && Self.matches(Self.swiftUIButton, line) {
                // Declarations, hidden shortcut carriers and role buttons (alert actions).
                if line.range(of: #"\b(struct|func|class|enum)\b|ButtonStyle"#, options: .regularExpression) != nil
                    || line.contains(#"Button("")"#) || line.contains("role:") { continue }
                let where_ = "\(file):\(i + 1): \(line.trimmingCharacters(in: .whitespaces))"
                switch Self.governingStyle(lines, i) {
                case "system"?: continue
                case nil: unstyled.append(where_)
                case let style?:
                    if Self.matches(Self.plainStyle, style) && !style.contains(Self.hitAreaMarker)
                        && Self.matches(Self.titledStart, line) {
                        plainTitled.append(where_)
                    }
                }
            }
        }
        #expect(unstyled.isEmpty, """
            these buttons have no style, so they render as system push buttons — give each a DS role \
            (.dsPrimary for a sheet's default action, .dsSecondary beside it), on the button or its container:
            \(unstyled.joined(separator: "\n"))
            """)
        #expect(plainTitled.isEmpty, """
            a titled action on .plainHit reads as plain text — give it a DS role, or mark a real hit area \
            "// ds-guard: hit area":
            \(plainTitled.joined(separator: "\n"))
            """)
    }

    @Test("the per-button check sees a button's own style, its container's, and menus")
    func perButtonCheckSamples() {
        let own = ["Button(\"Save\") { save() }", "    .buttonStyle(.dsPrimary)"]
        #expect(Self.governingStyle(own, 0)?.contains(".dsPrimary") == true)
        let container = ["HStack {", "    Button(\"A\") { }", "    Button(\"B\") { }", "}", ".buttonStyle(.dsSecondary)"]
        #expect(Self.governingStyle(container, 1)?.contains(".dsSecondary") == true)
        let bare = ["VStack {", "    Button(\"A\") { }", "    Text(\"x\")", "}", ".padding()"]
        #expect(Self.governingStyle(bare, 1) == nil)
        let menu = ["Menu {", "    Button(\"A\") { }", "} label: {", "    Text(\"More\")", "}"]
        #expect(Self.governingStyle(menu, 1) == "system")
    }

    @Test("the allowlist names only files that exist")
    func allowlistIsCurrent() {
        for file in Array(Self.allowlist.keys) + Array(Self.systemButtonFiles.keys) {
            #expect(FileManager.default.fileExists(atPath: Self.sourceRoot.appendingPathComponent(file).path),
                    "\(file) is allowlisted but gone — drop it from the allowlist")
        }
    }
}
