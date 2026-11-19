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

    @Test("the app bundles the six faces and both OFL licence texts")
    func bundlesFacesAndLicences() {
        let faces = Self.bundled("ttf")
        for face in DS.BrandFont.newsreaderFaces + DS.BrandFont.jetBrainsMonoFaces {
            #expect(faces.contains("\(face).ttf"), "\(face).ttf isn't in the app bundle")
        }
        let texts = Self.bundled("txt")
        #expect(texts.contains("Newsreader-OFL.txt"))
        #expect(texts.contains("JetBrainsMono-OFL.txt"))
    }

    @Test("every bundled face registers and loads by name")
    func facesLoad() {
        #expect(DS.BrandFont.available)
        for face in DS.BrandFont.newsreaderFaces + DS.BrandFont.jetBrainsMonoFaces {
            #expect(NSFont(name: face, size: 13) != nil, "\(face) didn't load")
        }
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
