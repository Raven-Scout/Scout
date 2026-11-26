import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Audit #106: the brand fonts ship inside the app, with their licences, and
/// the serif and mono voices draw with them on a Mac that never installed
/// them.
@MainActor
@Suite("Bundled brand fonts")
struct BrandFontTests {

    static func bundled(_ ext: String) -> Set<String> {
        guard let resources = Bundle.main.resourceURL,
              let files = FileManager.default.enumerator(at: resources, includingPropertiesForKeys: nil)
        else { return [] }
        var names: Set<String> = []
        for case let url as URL in files where url.pathExtension == ext { names.insert(url.lastPathComponent) }
        return names
    }

    @Test("the app bundles the nine faces and both OFL licence texts")
    func bundlesFacesAndLicences() {
        let faces = Self.bundled("ttf")
        for face in DS.BrandFont.allFaces {
            #expect(faces.contains("\(face).ttf"), "\(face).ttf isn't in the app bundle")
        }
        let texts = Self.bundled("txt")
        #expect(texts.contains("Newsreader-OFL.txt"))
        #expect(texts.contains("JetBrainsMono-OFL.txt"))
    }

    @Test("every bundled face registers and loads by name")
    func facesLoad() {
        #expect(DS.BrandFont.available)
        for face in DS.BrandFont.allFaces {
            #expect(NSFont(name: face, size: 13) != nil, "\(face) didn't load")
        }
    }

    @Test("italic serif text gets Newsreader's drawn italic at each weight")
    func italicsResolveToRealFaces() throws {
        #expect(DS.BrandFont.available)
        for (upright, italic) in zip(DS.BrandFont.newsreaderFaces, DS.BrandFont.newsreaderItalicFaces) {
            let font = try #require(NSFont(name: upright, size: 13))
            let converted = NSFontManager.shared.convert(font, toHaveTrait: .italicFontMask)
            #expect(converted.fontName == italic, "\(upright) italic resolved to \(converted.fontName)")
        }
    }

    @Test("the KB web pages load every brand face from a file next to them")
    func kbPagesLoadBundledFaces() throws {
        let css = try #require(Bundle.main.url(forResource: "kb-common", withExtension: "css"))
        let text = try String(contentsOf: css, encoding: .utf8)
        let page = try #require(Bundle.main.url(forResource: "kb-reader", withExtension: "html"))
        for face in DS.BrandFont.allFaces {
            #expect(text.contains(#"url("\#(face).ttf")"#), "kb-common.css doesn't load \(face)")
            // The web view may read only the reader's own folder, so the font sits there.
            let file = page.deletingLastPathComponent().appendingPathComponent("\(face).ttf")
            #expect(FileManager.default.fileExists(atPath: file.path), "\(face).ttf isn't beside kb-reader.html")
        }
        #expect(text.contains(#"--font-serif: "Scout Newsreader""#))
        #expect(text.contains(#"--font-mono: "Scout JetBrains Mono""#))
    }

    @Test("bold in the KB editor is the SemiBold face, not a regular one")
    func editorBoldIsSemiBold() {
        #expect(DS.BrandFont.available)
        let body = KBLiveEditor.Coordinator.serif(13)
        #expect(body.fontName == "Newsreader16pt-Regular")
        #expect(KBLiveEditor.Coordinator.bold(body).fontName == "Newsreader16pt-SemiBold")
        let italic = KBLiveEditor.Coordinator.italic(body)
        #expect(italic.fontName == "Newsreader16pt-Italic")
        #expect(KBLiveEditor.Coordinator.bold(italic).fontName == "Newsreader16pt-SemiBoldItalic")
        #expect(KBLiveEditor.Coordinator.bold(KBLiveEditor.Coordinator.mono(12)).fontName == "JetBrainsMono-SemiBold")
        #expect(DS.BrandFont.boldFace(of: "Helvetica") == nil)
    }

    @Test("each weight maps to a real face, never a synthesised one")
    func weightsMapToFaces() {
        #expect(DS.BrandFont.newsreader(.regular) == "Newsreader16pt-Regular")
        #expect(DS.BrandFont.newsreader(.light) == "Newsreader16pt-Regular")
        #expect(DS.BrandFont.newsreader(.medium) == "Newsreader16pt-Medium")
        #expect(DS.BrandFont.newsreader(.semibold) == "Newsreader16pt-SemiBold")
        #expect(DS.BrandFont.newsreader(.bold) == "Newsreader16pt-SemiBold")
        #expect(DS.BrandFont.jetBrainsMono(.medium) == "JetBrainsMono-Medium")
        #expect(DS.BrandFont.jetBrainsMono(.heavy) == "JetBrainsMono-SemiBold")
    }

    @Test("the reading and data voices draw with the brand faces")
    func voicesUseBrandFaces() {
        #expect(DS.font(.body, .serif) == Font.custom("Newsreader16pt-Regular", size: DS.scaled(13)))
        #expect(DS.font(.caption, .mono, weight: .medium) == Font.custom("JetBrainsMono-Medium", size: DS.scaled(11.5)))
    }
}
