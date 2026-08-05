import Foundation

/// In-memory `MemoryRepository` for tests and SwiftUI previews. The on-device
/// persisted implementation lives in Persistence/LocalMemoryRepository.swift.
public final class InMemoryMemoryRepository: MemoryRepository, @unchecked Sendable {
    private let lock = NSLock()
    private var records: [UUID: MemoryRecord] = [:]

    public init() {}

    public func save(_ record: MemoryRecord) async throws {
        lock.lock(); defer { lock.unlock() }
        records[record.id] = record
    }

    public func search(_ query: MemoryQuery) async throws -> [MemorySearchResult] {
        lock.lock(); defer { lock.unlock() }
        let needle = query.text.lowercased()
        let candidates = records.values.filter { !$0.isDeleted }
        let scored: [MemorySearchResult] = candidates.compactMap { record in
            let haystack = (record.title + " " + record.normalizedSummary + " " + record.tags.joined(separator: " ")).lowercased()
            let matches = needle.split(separator: " ").filter { haystack.contains($0) }.count
            guard matches > 0 else { return nil }
            let recencyBoost = 1.0 / (1.0 + Date().timeIntervalSince(record.timestamp) / 86_400)
            return MemorySearchResult(record: record, relevanceScore: Double(matches) + recencyBoost)
        }
        return scored.sorted { $0.relevanceScore > $1.relevanceScore }.prefix(query.limit).map { $0 }
    }

    public func delete(id: UUID) async throws {
        lock.lock(); defer { lock.unlock() }
        records[id]?.deletedAt = Date()
    }

    public func deleteAll() async throws {
        lock.lock(); defer { lock.unlock() }
        let now = Date()
        for key in records.keys { records[key]?.deletedAt = now }
    }

    public func all() async throws -> [MemoryRecord] {
        lock.lock(); defer { lock.unlock() }
        return records.values.filter { !$0.isDeleted }.sorted { $0.timestamp > $1.timestamp }
    }

    public func export() async throws -> Data {
        lock.lock(); defer { lock.unlock() }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return try encoder.encode(Array(records.values))
    }
}
