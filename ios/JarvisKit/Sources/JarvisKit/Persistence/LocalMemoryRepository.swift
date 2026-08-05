import Foundation
import SwiftData

@Model
final class PersistedMemoryRecord {
    @Attribute(.unique) var id: UUID
    var type: String
    var title: String
    var originalUserText: String
    var normalizedSummary: String
    var timestamp: Date
    var latitude: Double?
    var longitude: Double?
    var coarseLocationName: String?
    var imageReference: String?
    var extractedText: String?
    var objectLabels: [String]
    var tags: [String]
    var embedding: [Float]?
    var retentionPolicy: String
    var sensitivity: String
    var createdBy: String
    var deletedAt: Date?

    init(from record: MemoryRecord) {
        id = record.id
        type = record.type.rawValue
        title = record.title
        originalUserText = record.originalUserText
        normalizedSummary = record.normalizedSummary
        timestamp = record.timestamp
        latitude = record.latitude
        longitude = record.longitude
        coarseLocationName = record.coarseLocationName
        imageReference = record.imageReference
        extractedText = record.extractedText
        objectLabels = record.objectLabels
        tags = record.tags
        embedding = record.embedding
        retentionPolicy = record.retentionPolicy.rawValue
        sensitivity = record.sensitivity.rawValue
        createdBy = record.createdBy
        deletedAt = record.deletedAt
    }

    func toRecord() -> MemoryRecord {
        MemoryRecord(
            id: id,
            type: MemoryType(rawValue: type) ?? .note,
            title: title,
            originalUserText: originalUserText,
            normalizedSummary: normalizedSummary,
            timestamp: timestamp,
            latitude: latitude,
            longitude: longitude,
            coarseLocationName: coarseLocationName,
            imageReference: imageReference,
            extractedText: extractedText,
            objectLabels: objectLabels,
            tags: tags,
            embedding: embedding,
            retentionPolicy: RetentionPolicy(rawValue: retentionPolicy) ?? .days365,
            sensitivity: Sensitivity(rawValue: sensitivity) ?? .normal,
            createdBy: createdBy,
            deletedAt: deletedAt
        )
    }
}

/// On-device SwiftData-backed `MemoryRepository`. This is the default
/// repository for both demo mode and live mode — a backend sync layer, if
/// configured, wraps this same protocol rather than replacing it, so
/// memories are always readable offline.
@ModelActor
public actor LocalMemoryRepository: MemoryRepository {
    public static func makeContainer(inMemoryOnly: Bool = false) throws -> ModelContainer {
        let schema = Schema([PersistedMemoryRecord.self])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: inMemoryOnly)
        return try ModelContainer(for: schema, configurations: [configuration])
    }

    public func save(_ record: MemoryRecord) async throws {
        let context = modelContext
        let persisted = PersistedMemoryRecord(from: record)
        context.insert(persisted)
        try context.save()
    }

    public func search(_ query: MemoryQuery) async throws -> [MemorySearchResult] {
        let context = modelContext
        let descriptor = FetchDescriptor<PersistedMemoryRecord>(sortBy: [SortDescriptor(\.timestamp, order: .reverse)])
        let all = try context.fetch(descriptor)
        let needle = query.text.lowercased()
        let scored: [MemorySearchResult] = all
            .filter { $0.deletedAt == nil }
            .compactMap { persisted in
                let record = persisted.toRecord()
                if !query.types.isEmpty && !query.types.contains(record.type) { return nil }
                let haystack = (record.title + " " + record.normalizedSummary + " " + record.tags.joined(separator: " ")).lowercased()
                let matches = needle.split(separator: " ").filter { haystack.contains($0) }.count
                guard matches > 0 else { return nil }
                let recencyBoost = 1.0 / (1.0 + Date().timeIntervalSince(record.timestamp) / 86_400)
                return MemorySearchResult(record: record, relevanceScore: Double(matches) + recencyBoost)
            }
        return Array(scored.sorted { $0.relevanceScore > $1.relevanceScore }.prefix(query.limit))
    }

    public func delete(id: UUID) async throws {
        let context = modelContext
        let descriptor = FetchDescriptor<PersistedMemoryRecord>(predicate: #Predicate { $0.id == id })
        if let record = try context.fetch(descriptor).first {
            record.deletedAt = Date()
            try context.save()
        }
    }

    public func deleteAll() async throws {
        let context = modelContext
        let descriptor = FetchDescriptor<PersistedMemoryRecord>()
        let all = try context.fetch(descriptor)
        let now = Date()
        for record in all { record.deletedAt = now }
        try context.save()
    }

    public func all() async throws -> [MemoryRecord] {
        let context = modelContext
        let descriptor = FetchDescriptor<PersistedMemoryRecord>(sortBy: [SortDescriptor(\.timestamp, order: .reverse)])
        return try context.fetch(descriptor).filter { $0.deletedAt == nil }.map { $0.toRecord() }
    }

    public func export() async throws -> Data {
        let records = try await all()
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return try encoder.encode(records)
    }
}
