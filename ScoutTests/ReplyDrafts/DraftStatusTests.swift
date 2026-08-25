import Testing
import Foundation
@testable import Scout

@Suite("DraftStatus")
struct DraftStatusTests {

    @Test func parsesKnownWordsCaseInsensitively() {
        #expect(DraftStatus.parse("draft") == .draft)
        #expect(DraftStatus.parse("Sent") == .sent)
        #expect(DraftStatus.parse("  DISMISSED  ") == .dismissed)
    }

    @Test func emptyValueDefaultsToDraft() {
        #expect(DraftStatus.parse("") == .draft)
        #expect(DraftStatus.parse("   ") == .draft)
    }

    @Test func unknownValuePreservedVerbatim() {
        #expect(DraftStatus.parse("queued") == .unknown("queued"))
    }

    @Test func onlyDraftIsAwaitingAction() {
        #expect(DraftStatus.draft.isAwaitingAction)
        #expect(!DraftStatus.sent.isAwaitingAction)
        #expect(!DraftStatus.dismissed.isAwaitingAction)
        #expect(!DraftStatus.unknown("x").isAwaitingAction)
    }

    @Test func fileValueIsCanonicalLowercaseContract() {
        // Round-trips with the plugin's `status:` vocabulary.
        #expect(DraftStatus.draft.fileValue == "draft")
        #expect(DraftStatus.sent.fileValue == "sent")
        #expect(DraftStatus.dismissed.fileValue == "dismissed")
        for s in [DraftStatus.draft, .sent, .dismissed] {
            #expect(DraftStatus.parse(s.fileValue) == s)
        }
    }

    @Test func displayNameIsTitleCased() {
        #expect(DraftStatus.draft.displayName == "Draft")
        #expect(DraftStatus.sent.displayName == "Sent")
        #expect(DraftStatus.dismissed.displayName == "Dismissed")
        #expect(DraftStatus.unknown("queued").displayName == "queued")
    }
}

@Suite("DraftChannel")
struct DraftChannelTests {

    @Test func parsesKnownChannels() {
        #expect(DraftChannel.parse("email") == .email)
        #expect(DraftChannel.parse("Slack") == .slack)
        #expect(DraftChannel.parse("LINEAR") == .linear)
        #expect(DraftChannel.parse("github") == .github)
        #expect(DraftChannel.parse("whatsapp") == .whatsapp)
        #expect(DraftChannel.parse("signal") == .other("signal"))
    }

    @Test func subjectChannelsUseSubject() {
        #expect(DraftChannel.email.usesSubject)
        #expect(DraftChannel.linear.usesSubject)
        #expect(DraftChannel.github.usesSubject)
        #expect(!DraftChannel.slack.usesSubject)
        #expect(!DraftChannel.whatsapp.usesSubject)
    }

    @Test func openActionLabelIsChannelSpecific() {
        #expect(DraftChannel.email.openActionLabel == "Open in Gmail")
        #expect(DraftChannel.slack.openActionLabel == "Open in Slack")
        #expect(DraftChannel.linear.openActionLabel == "Open in Linear")
        #expect(DraftChannel.github.openActionLabel == "Open in GitHub")
    }
}
