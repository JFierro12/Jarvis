import Foundation

public struct MemoryQuery: Sendable, Equatable {
    public let text: String
    public let types: [MemoryType]
    public let limit: Int

    public init(text: String, types: [MemoryType] = [], limit: Int = 5) {
        self.text = text
        self.types = types
        self.limit = limit
    }
}

/// Local-first memory storage. A backend sync layer, if configured, sits
/// behind this same protocol so the rest of the app never knows whether a
/// memory is local-only or synced.
public protocol MemoryRepository: AnyObject, Sendable {
    func save(_ record: MemoryRecord) async throws
    func search(_ query: MemoryQuery) async throws -> [MemorySearchResult]
    func delete(id: UUID) async throws
    func deleteAll() async throws
    func all() async throws -> [MemoryRecord]
    func export() async throws -> Data
}
