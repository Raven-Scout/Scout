import Foundation
import Testing
@testable import Scout

@Suite("DisplayProfileCodec")
struct DisplayProfileCodecTests {
    private static func decode(_ json: String) -> Result<DisplayProfileCodec.Decoded, DisplayProfileCodec.Failure> {
        DisplayProfileCodec.decode(Data(json.utf8))
    }

    private static func profile(_ change: (inout ActionItemsDisplay) -> Void) -> DisplayProfile {
        var p = DisplayProfile()
        change(&p.actionItems)
        return p
    }

    // MARK: decode: files that apply

    static let valid: [(String, DisplayProfile)] = [
        ("", DisplayProfile()),
        ("  \n", DisplayProfile()),
        ("{}", DisplayProfile()),
        (#"{"schema":1}"#, DisplayProfile()),
        (#"{"schema":1,"actionItems":{"defaultView":"board"}}"#, profile { $0.defaultView = .board }),
        (#"{"schema":1,"actionItems":{"defaultView":"topics"}}"#, profile { $0.defaultView = .topics }),
        (#"{"schema":1,"actionItems":{"topics":{"doneRange":"today"}}}"#, profile { $0.topics.doneRange = .today }),
        (#"{"schema":1,"actionItems":{"sort":"alphabetical","group":"none","density":"compact"}}"#,
         profile { $0.sort = .alphabetical; $0.grouping = .none; $0.density = .compact }),
        (#"{"schema":1,"actionItems":{"fields":{"refs":false,"snooze":false,"comments":true}}}"#,
         profile { $0.fields = .init(refs: false, snooze: false, comments: true) }),
        (#"{"schema":1.0,"actionItems":{"sort":"alphabetical"}}"#, profile { $0.sort = .alphabetical }),
    ]

    @Test(arguments: valid)
    func validFilesApply(_ json: String, _ expected: DisplayProfile) throws {
        let decoded = try Self.decode(json).get()
        #expect(decoded.profile == expected)
        #expect(decoded.warnings.isEmpty)
    }

    // MARK: decode: files that apply with warnings

    static let warned: [(String, DisplayProfile, [String])] = [
        (#"{"actionItems":{"density":"compact"}}"#, profile { $0.density = .compact },
         [#""schema" is missing; reading it as 1"#]),
        (#"{"schema":1,"theme":"dark"}"#, DisplayProfile(), [#"unknown key "theme""#]),
        (#"{"schema":1,"sidebar":{"order":["kb"]}}"#, DisplayProfile(), [#"unknown key "sidebar""#]),
        (#"{"schema":1,"actionItems":{"foo":1,"sort":"alphabetical"}}"#, profile { $0.sort = .alphabetical },
         [#"unknown key "actionItems.foo""#]),
        (#"{"schema":1,"actionItems":{"fields":{"plan":true}}}"#, DisplayProfile(),
         [#"unknown key "actionItems.fields.plan""#]),
        (#"{"schema":1,"actionItems":{"sort":"oldestFirst","density":"compact"}}"#, profile { $0.density = .compact },
         [#""actionItems.sort" has an unsupported value; using the default"#]),
        (#"{"schema":1,"actionItems":{"fields":{"refs":"no","snooze":0}}}"#, DisplayProfile(),
         [#""actionItems.fields.refs" is not true or false; using the default"#,
          #""actionItems.fields.snooze" is not true or false; using the default"#]),
        (#"{"schema":1,"actionItems":[]}"#, DisplayProfile(), [#""actionItems" is not an object"#]),
        (#"{"schema":1,"actionItems":{"topics":{"doneRange":"month","legend":"mine"}}}"#, DisplayProfile(),
         [#"unknown key "actionItems.topics.legend""#,
          #""actionItems.topics.doneRange" has an unsupported value; using the default"#]),
        (#"{"schema":1,"actionItems":{"fields":true,"topics":{"doneRange":"today"}}}"#,
         profile { $0.topics.doneRange = .today },
         [#""actionItems.fields" is not an object"#]),
    ]

    @Test(arguments: warned)
    func warnedFilesApplyWhatTheyCan(_ json: String, _ expected: DisplayProfile, _ warnings: [String]) throws {
        let decoded = try Self.decode(json).get()
        #expect(decoded.profile == expected)
        #expect(decoded.warnings == warnings)
    }

    // MARK: decode: files the app must not use

    static let refused: [(String, DisplayProfileCodec.Failure)] = [
        ("{ not json", .unreadable("not valid JSON")),
        ("[1, 2]", .unreadable("the top level is not an object")),
        (#"{"schema":"1"}"#, .unreadable(#""schema" is not a whole number"#)),
        (#"{"schema":1.5}"#, .unreadable(#""schema" is not a whole number"#)),
        (#"{"schema":0}"#, .unreadable(#""schema" must be 1 or higher"#)),
        (#"{"schema":2,"actionItems":{"defaultView":"board"}}"#, .unsupportedSchema(2)),
    ]

    @Test(arguments: refused)
    func unusableFilesAreRefused(_ json: String, _ failure: DisplayProfileCodec.Failure) {
        #expect(Self.decode(json) == .failure(failure))
    }

    // MARK: patch

    private static func object(_ data: Data) throws -> NSDictionary {
        try #require(try JSONSerialization.jsonObject(with: data) as? NSDictionary)
    }

    @Test func firstWriteHoldsOnlyTheChangeAndTheSchema() throws {
        let entries = Self.profile { $0.defaultView = .board }.changedEntries(from: DisplayProfile())
        let data = try DisplayProfileCodec.patch(nil, with: entries)
        #expect(try Self.object(data) == ["schema": 1, "actionItems": ["defaultView": "board"]] as NSDictionary)
    }

    @Test func patchPreservesWhatTheAppDoesNotUnderstand() throws {
        let existing = Data(#"{"schema":1,"sidebar":{"order":["kb"]},"theme":"dark","actionItems":{"sort":"planned","foo":[1],"fields":{"tags":true}}}"#.utf8)
        let entries = Self.profile { $0.density = .compact }.changedEntries(from: DisplayProfile())
        let data = try DisplayProfileCodec.patch(existing, with: entries)
        let expected: NSDictionary = [
            "schema": 1,
            "sidebar": ["order": ["kb"]],
            "theme": "dark",
            "actionItems": ["sort": "planned", "foo": [1], "density": "compact", "fields": ["tags": true]],
        ]
        #expect(try Self.object(data) == expected)
    }

    @Test func patchAddsAMissingSchema() throws {
        let existing = Data(#"{"actionItems":{"sort":"alphabetical"}}"#.utf8)
        let entries = Self.profile { $0.fields.comments = true }.changedEntries(from: DisplayProfile())
        let data = try DisplayProfileCodec.patch(existing, with: entries)
        #expect(try Self.object(data)["schema"] as? Int == 1)
    }

    @Test(arguments: ["{ not json", #"{"schema":2}"#])
    func patchRefusesFilesItMustNotRewrite(_ json: String) {
        #expect(throws: DisplayProfileCodec.Failure.self) {
            try DisplayProfileCodec.patch(Data(json.utf8), with: [])
        }
    }

    @Test func outputIsPrettySortedAndEndsInANewline() throws {
        let entries = Self.profile { $0.sort = .alphabetical; $0.defaultView = .board }.changedEntries(from: DisplayProfile())
        let text = String(decoding: try DisplayProfileCodec.patch(nil, with: entries), as: UTF8.self)
        #expect(text == """
        {
          "actionItems" : {
            "defaultView" : "board",
            "sort" : "alphabetical"
          },
          "schema" : 1
        }

        """)
    }

    @Test func changedEntriesListOnlyDifferences() {
        let old = DisplayProfile()
        let new = Self.profile { $0.grouping = .none; $0.fields.refs = false }
        #expect(new.changedEntries(from: old) == [
            .init(path: ["actionItems", "group"], value: .string("none")),
            .init(path: ["actionItems", "fields", "refs"], value: .bool(false)),
        ])
        #expect(old.changedEntries(from: old).isEmpty)
    }
}
