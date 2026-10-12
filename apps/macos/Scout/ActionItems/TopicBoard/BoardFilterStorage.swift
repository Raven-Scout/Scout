import Foundation

/// The legend filter as a comma-separated `@AppStorage` string.
nonisolated enum BoardFilterStorage {
    static func decode(_ raw: String) -> Set<BoardState> {
        Set(raw.split(separator: ",").compactMap { BoardState(rawValue: String($0)) }.filter(BoardState.filterable.contains))
    }

    static func encode(_ filter: Set<BoardState>) -> String {
        BoardState.filterable.filter(filter.contains).map(\.rawValue).joined(separator: ",")
    }

    static func toggling(_ state: BoardState, in raw: String) -> String {
        var filter = decode(raw)
        if filter.remove(state) == nil { filter.insert(state) }
        return encode(filter)
    }
}
