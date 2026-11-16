import SwiftUI
import Testing
@testable import Scout

/// The text-size knob and the Reduce Motion rules (C7).
@Suite("Text size")
struct TextScaleTests {

    private func freshDefaults() -> UserDefaults {
        let name = "TextScaleTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    @Test("standard size leaves every design size untouched")
    func standardIsIdentity() {
        for size in [8.5, 11, 13, 28] as [CGFloat] {
            #expect(DS.scaledSize(size, factor: 1) == size)
        }
    }

    @Test("larger sizes scale every size by the same factor, to the half point")
    func scalesConsistently() {
        #expect(DS.scaledSize(13, factor: 1.25) == 16.5)   // 16.25 → 16.5
        #expect(DS.scaledSize(10, factor: 1.4) == 14)
        #expect(DS.scaledSize(11.5, factor: 1.12) == 13)    // 12.88 → 13
        for size in [9, 11, 13, 18, 28] as [CGFloat] {
            let scaled = DS.scaledSize(size, factor: 1.4)
            #expect(scaled > size)
            #expect((scaled * 2).rounded() == scaled * 2, "\(scaled) is not on a half point")
        }
    }

    @Test("the sizes grow in order and Standard is 1×")
    func sizesAreOrdered() {
        let factors = TextScale.Size.allCases.map(\.factor)
        #expect(factors.first == 1)
        #expect(factors == factors.sorted())
        #expect(Set(factors).count == factors.count)
    }

    @Test("the semantic type scale grows from caption to display")
    func semanticScaleIsOrdered() {
        let sizes = DS.TextStyle.allCases.map(\.pointSize)
        #expect(sizes == sizes.sorted())
        #expect(DS.TextStyle.body.pointSize == 13)   // the macOS body size
    }

    @MainActor
    @Test("the chosen size persists and is read back")
    func persists() {
        let defaults = freshDefaults()
        let scale = TextScale(defaults: defaults)
        #expect(scale.size == .standard)
        scale.size = .larger
        #expect(defaults.string(forKey: TextScale.defaultsKey) == "larger")
        #expect(TextScale(defaults: defaults).size == .larger)
    }

    @MainActor
    @Test("an unknown stored value falls back to Standard")
    func unknownValueFallsBack() {
        let defaults = freshDefaults()
        defaults.set("enormous", forKey: TextScale.defaultsKey)
        #expect(TextScale(defaults: defaults).size == .standard)
    }

    @MainActor
    @Test("DS fonts are built at the scaled size for every voice and style")
    func fontsResolve() {
        for style in DS.TextStyle.allCases {
            for voice in [DS.Voice.sans, .serif, .mono] {
                _ = DS.font(style, voice, weight: .medium)   // must not trap
            }
        }
        #expect(DS.font(.body) == DS.sans(13))
        #expect(DS.font(.caption, .mono) == DS.mono(11.5))
    }
}

@Suite("Reduce Motion")
struct MotionTests {

    @Test("an animation is dropped under Reduce Motion and kept otherwise")
    func animationRule() {
        #expect(Motion.animation(.easeInOut, reduceMotion: true) == nil)
        #expect(Motion.animation(.easeInOut, reduceMotion: false) == .easeInOut)
        #expect(Motion.animation(nil, reduceMotion: false) == nil)
    }

    @MainActor
    @Test("the motion modifiers render")
    func modifiersRender() {
        // `accessibilityReduceMotion` is read-only in the environment, so this
        // renders under the machine's setting; the rules above cover both.
        ViewHost.render(
            Text("x")
                .motionAnimation(.easeInOut, value: 1),
            size: CGSize(width: 100, height: 40))
    }

    @MainActor
    @Test("withMotion always applies the change")
    func withMotionApplies() {
        var value = 0
        withMotion(.easeInOut) { value = 1 }
        #expect(value == 1)
    }
}
