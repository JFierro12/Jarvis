import Foundation

public final class MockSmartHomeClient: SmartHomeClient, @unchecked Sendable {
    private var entityStates: [String: SmartHomeEntity]

    public init() {
        entityStates = [
            "light.bedroom": SmartHomeEntity(entityId: "light.bedroom", friendlyName: "Bedroom Light", domain: "light", state: "on")
        ]
    }

    public func entities() async throws -> [SmartHomeEntity] {
        Array(entityStates.values)
    }

    public func setState(entityId: String, state: String) async throws -> SmartHomeEntity {
        guard var entity = entityStates[entityId] else { throw SmartHomeError.entityNotAllowlisted }
        entity = SmartHomeEntity(entityId: entity.entityId, friendlyName: entity.friendlyName, domain: entity.domain, state: state)
        entityStates[entityId] = entity
        return entity
    }
}
