import Foundation

public enum MemoryType: String, Codable, Sendable, CaseIterable {
    case scene
    case objectLocation
    case note
    case project
    case task
    case preference
    case eventReference
    case temporaryConversational
}

public enum RetentionPolicy: String, Codable, Sendable, CaseIterable {
    case sessionOnly
    case days30
    case days365
    case untilDeleted
}

public enum Sensitivity: String, Codable, Sendable, CaseIterable {
    case normal
    case sensitive
}

/// A single user-controlled memory. Nothing populates this table implicitly —
/// every row traces back to an explicit "remember this" moment.
public struct MemoryRecord: Codable, Sendable, Identifiable, Equatable {
    public var id: UUID
    public var type: MemoryType
    public var title: String
    public var originalUserText: String
    public var normalizedSummary: String
    public var timestamp: Date
    public var latitude: Double?
    public var longitude: Double?
    public var coarseLocationName: String?
    public var imageReference: String?
    public var extractedText: String?
    public var objectLabels: [String]
    public var tags: [String]
    public var embedding: [Float]?
    public var retentionPolicy: RetentionPolicy
    public var sensitivity: Sensitivity
    public var createdBy: String
    public var deletedAt: Date?

    public init(
        id: UUID = UUID(),
        type: MemoryType,
        title: String,
        originalUserText: String,
        normalizedSummary: String,
        timestamp: Date = Date(),
        latitude: Double? = nil,
        longitude: Double? = nil,
        coarseLocationName: String? = nil,
        imageReference: String? = nil,
        extractedText: String? = nil,
        objectLabels: [String] = [],
        tags: [String] = [],
        embedding: [Float]? = nil,
        retentionPolicy: RetentionPolicy = .days365,
        sensitivity: Sensitivity = .normal,
        createdBy: String = "user",
        deletedAt: Date? = nil
    ) {
        self.id = id
        self.type = type
        self.title = title
        self.originalUserText = originalUserText
        self.normalizedSummary = normalizedSummary
        self.timestamp = timestamp
        self.latitude = latitude
        self.longitude = longitude
        self.coarseLocationName = coarseLocationName
        self.imageReference = imageReference
        self.extractedText = extractedText
        self.objectLabels = objectLabels
        self.tags = tags
        self.embedding = embedding
        self.retentionPolicy = retentionPolicy
        self.sensitivity = sensitivity
        self.createdBy = createdBy
        self.deletedAt = deletedAt
    }

    public var isDeleted: Bool { deletedAt != nil }
}

public struct MemorySearchResult: Sendable, Equatable {
    public let record: MemoryRecord
    public let relevanceScore: Double

    public init(record: MemoryRecord, relevanceScore: Double) {
        self.record = record
        self.relevanceScore = relevanceScore
    }
}
