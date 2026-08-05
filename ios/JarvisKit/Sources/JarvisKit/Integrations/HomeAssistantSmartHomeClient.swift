import Foundation

/// Home Assistant REST adapter, restricted to low-risk domains by default
/// (spec §20). The long-lived access token lives in Keychain, never in this
/// repository — see `KeychainStore`. Disabled unless `enableSmartHome` and a
/// base URL/token are configured.
public final class HomeAssistantSmartHomeClient: SmartHomeClient, @unchecked Sendable {
    private struct HAState: Decodable {
        let entity_id: String
        let state: String
        let attributes: [String: String]?
    }

    private let baseURL: URL
    private let tokenProvider: @Sendable () -> String?
    private let session: URLSession
    private let allowlistedEntityIds: Set<String>

    public init(baseURL: URL, allowlistedEntityIds: Set<String>, tokenProvider: @escaping @Sendable () -> String?, session: URLSession = .init(configuration: .ephemeral)) {
        self.baseURL = baseURL
        self.allowlistedEntityIds = allowlistedEntityIds
        self.tokenProvider = tokenProvider
        self.session = session
    }

    public func entities() async throws -> [SmartHomeEntity] {
        guard let token = tokenProvider() else { throw SmartHomeError.unavailable }
        var request = URLRequest(url: baseURL.appendingPathComponent("/api/states"))
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw SmartHomeError.unavailable }
        let states = try JSONDecoder().decode([HAState].self, from: data)
        return states
            .filter { allowlistedEntityIds.contains($0.entity_id) }
            .map { SmartHomeEntity(entityId: $0.entity_id, friendlyName: $0.attributes?["friendly_name"] ?? $0.entity_id, domain: String($0.entity_id.split(separator: ".").first ?? ""), state: $0.state) }
    }

    public func setState(entityId: String, state: String) async throws -> SmartHomeEntity {
        guard allowlistedEntityIds.contains(entityId) else { throw SmartHomeError.entityNotAllowlisted }
        let domain = String(entityId.split(separator: ".").first ?? "")
        guard SmartHomeEntity.lowRiskDomains.contains(domain) else { throw SmartHomeError.confirmationRequired }
        guard let token = tokenProvider() else { throw SmartHomeError.unavailable }

        let service = state == "on" ? "turn_on" : "turn_off"
        var request = URLRequest(url: baseURL.appendingPathComponent("/api/services/\(domain)/\(service)"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["entity_id": entityId])

        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { throw SmartHomeError.unavailable }
        return SmartHomeEntity(entityId: entityId, friendlyName: entityId, domain: domain, state: state)
    }
}
