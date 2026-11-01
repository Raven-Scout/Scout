import Foundation

/// Parsing and patching for `scout-display.json`, as pure functions. The rules
/// are spec section 4: an unknown key is reported and preserved, a bad value
/// falls back for that key only, and a file the app cannot fully read is never
/// rewritten.
nonisolated enum DisplayProfileCodec {
    static let fileName = "scout-display.json"

    enum Failure: Error, Equatable, Sendable {
        case unreadable(String)
        case unsupportedSchema(Int)
    }

    struct Decoded: Equatable, Sendable {
        var profile: DisplayProfile
        var warnings: [String]
    }

    static func decode(_ data: Data) -> Result<Decoded, Failure> {
        let root: [String: Any]
        switch object(from: data) {
        case .failure(let failure): return .failure(failure)
        case .success(let parsed): root = parsed
        }
        guard !root.isEmpty else { return .success(Decoded(profile: DisplayProfile(), warnings: [])) }

        var warnings: [String] = []
        if let raw = root["schema"] {
            guard let number = raw as? NSNumber, !isBool(number),
                  number.doubleValue == number.doubleValue.rounded() else {
                return .failure(.unreadable("\"schema\" is not a whole number"))
            }
            if number.intValue > DisplayProfile.currentSchema { return .failure(.unsupportedSchema(number.intValue)) }
            if number.intValue < 1 { return .failure(.unreadable("\"schema\" must be 1 or higher")) }
        } else {
            warnings.append("\"schema\" is missing; reading it as 1")
        }

        let known: Set<String> = ["schema", "actionItems"]
        for key in root.keys.sorted() where !known.contains(key) {
            warnings.append("unknown key \"\(key)\"")
        }

        var profile = DisplayProfile()
        if let raw = root["actionItems"] {
            if let section = raw as? [String: Any] {
                profile.actionItems = actionItems(section, warnings: &warnings)
            } else {
                warnings.append("\"actionItems\" is not an object")
            }
        }
        return .success(Decoded(profile: profile, warnings: warnings))
    }

    /// The bytes to write after applying `entries` to `existing`, the file's
    /// current bytes (`nil` when it does not exist). Every key not in `entries`
    /// is kept as it is. Throws when `existing` is a file the app may not
    /// rewrite.
    static func patch(_ existing: Data?, with entries: [DisplayProfile.Entry]) throws -> Data {
        var root: [String: Any] = [:]
        if let existing {
            if case .failure(let failure) = decode(existing) { throw failure }
            if case .success(let parsed) = object(from: existing) { root = parsed }
        }
        root["schema"] = DisplayProfile.currentSchema
        for entry in entries {
            set(entry.value.json, at: entry.path[...], in: &root)
        }
        var data = try JSONSerialization.data(
            withJSONObject: root,
            options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        )
        data.append(0x0A)
        return data
    }

    // MARK: - Private

    /// The root object. Whitespace-only content counts as an empty object, so
    /// an emptied file behaves like a missing one.
    private static func object(from data: Data) -> Result<[String: Any], Failure> {
        if data.allSatisfy({ $0 == 0x20 || $0 == 0x09 || $0 == 0x0A || $0 == 0x0D }) { return .success([:]) }
        guard let parsed = try? JSONSerialization.jsonObject(with: data) else {
            return .failure(.unreadable("not valid JSON"))
        }
        guard let root = parsed as? [String: Any] else {
            return .failure(.unreadable("the top level is not an object"))
        }
        return .success(root)
    }

    private static func actionItems(_ dict: [String: Any], warnings: inout [String]) -> ActionItemsDisplay {
        let path = "actionItems"
        let known: Set<String> = ["defaultView", "sort", "group", "density", "fields", "topics"]
        for key in dict.keys.sorted() where !known.contains(key) {
            warnings.append("unknown key \"\(path).\(key)\"")
        }
        var out = ActionItemsDisplay()
        if let v: ActionItemsViewMode = option(dict, "defaultView", in: path, &warnings) { out.defaultView = v }
        if let v: ActionItemsDisplay.Sort = option(dict, "sort", in: path, &warnings) { out.sort = v }
        if let v: ActionItemsDisplay.Grouping = option(dict, "group", in: path, &warnings) { out.grouping = v }
        if let v: ActionItemsDisplay.Density = option(dict, "density", in: path, &warnings) { out.density = v }
        if let fields = object(dict, "fields", in: path, known: ["refs", "snooze", "comments"], &warnings) {
            let fieldsPath = "\(path).fields"
            if let v = flag(fields, "refs", in: fieldsPath, &warnings) { out.fields.refs = v }
            if let v = flag(fields, "snooze", in: fieldsPath, &warnings) { out.fields.snooze = v }
            if let v = flag(fields, "comments", in: fieldsPath, &warnings) { out.fields.comments = v }
        }
        if let topics = object(dict, "topics", in: path, known: ["doneRange"], &warnings),
           let v: DoneRange = option(topics, "doneRange", in: "\(path).topics", &warnings) {
            out.topics.doneRange = v
        }
        return out
    }

    /// A nested object, with a warning for each key it doesn't know, or nil
    /// (and a warning) when the value is not an object.
    private static func object(
        _ dict: [String: Any], _ key: String, in path: String, known: Set<String>, _ warnings: inout [String]
    ) -> [String: Any]? {
        guard let raw = dict[key] else { return nil }
        guard let nested = raw as? [String: Any] else {
            warnings.append("\"\(path).\(key)\" is not an object")
            return nil
        }
        for name in nested.keys.sorted() where !known.contains(name) {
            warnings.append("unknown key \"\(path).\(key).\(name)\"")
        }
        return nested
    }

    private static func option<E: RawRepresentable>(
        _ dict: [String: Any], _ key: String, in path: String, _ warnings: inout [String]
    ) -> E? where E.RawValue == String {
        guard let raw = dict[key] else { return nil }
        if let string = raw as? String, let value = E(rawValue: string) { return value }
        warnings.append("\"\(path).\(key)\" has an unsupported value; using the default")
        return nil
    }

    private static func flag(
        _ dict: [String: Any], _ key: String, in path: String, _ warnings: inout [String]
    ) -> Bool? {
        guard let raw = dict[key] else { return nil }
        if let number = raw as? NSNumber, isBool(number) { return number.boolValue }
        warnings.append("\"\(path).\(key)\" is not true or false; using the default")
        return nil
    }

    /// `JSONSerialization` returns `true` and `1` as the same `NSNumber`
    /// class; only the CF type tells them apart.
    private static func isBool(_ number: NSNumber) -> Bool {
        CFGetTypeID(number) == CFBooleanGetTypeID()
    }

    private static func set(_ value: Any, at path: ArraySlice<String>, in dict: inout [String: Any]) {
        guard let key = path.first else { return }
        guard path.count > 1 else {
            dict[key] = value
            return
        }
        var child = dict[key] as? [String: Any] ?? [:]
        set(value, at: path.dropFirst(), in: &child)
        dict[key] = child
    }
}

private extension DisplayProfile.Entry.Value {
    nonisolated var json: Any {
        switch self {
        case .string(let s): return s
        case .bool(let b):   return b
        }
    }
}
