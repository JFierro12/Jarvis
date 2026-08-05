import Foundation

public struct SmartHomeEntity: Sendable, Equatable, Codable {
    public let entityId: String
    public let friendlyName: String
    public let domain: String
    public let state: String

    public init(entityId: String, friendlyName: String, domain: String, state: String) {
        self.entityId = entityId
        self.friendlyName = friendlyName
        self.domain = domain
        self.state = state
    }

    /// Only low-risk domains are controllable without confirmation. Locks,
    /// doors, garage doors, alarms, and climate extremes always require
    /// confirmation regardless of this allowlist — see PolicyEngine.
    public static let lowRiskDomains: Set<String> = ["light", "switch", "fan"]
}

public enum SmartHomeError: Error, Equatable {
    case unavailable
    case entityNotAllowlisted
    case confirmationRequired
}

public protocol SmartHomeClient: AnyObject, Sendable {
    func entities() async throws -> [SmartHomeEntity]
    func setState(entityId: String, state: String) async throws -> SmartHomeEntity
}
