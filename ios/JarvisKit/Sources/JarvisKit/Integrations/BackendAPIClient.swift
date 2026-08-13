import Foundation

public enum BackendAPIError: Error, Equatable {
    case notConfigured
    case requestFailed(Int)
    case decodingFailed
    case timeout
}

/// Thin `URLSession` wrapper for the FastAPI backend. Holds no secrets itself —
/// the bearer token is fetched from `KeychainStore` by the caller. No
/// automatic retry for non-idempotent requests (spec §25).
public final class BackendAPIClient: @unchecked Sendable {
    private let baseURL: URL
    private let session: URLSession
    private let tokenProvider: @Sendable () -> String?

    public init(baseURL: URL, session: URLSession = .init(configuration: .ephemeral), tokenProvider: @escaping @Sendable () -> String? = { nil }) {
        self.baseURL = baseURL
        self.session = session
        self.tokenProvider = tokenProvider
    }

    public func post<Request: Encodable, Response: Decodable>(
        path: String,
        body: Request,
        timeout: TimeInterval = 10
    ) async throws -> Response {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(UUID().uuidString, forHTTPHeaderField: "X-Request-Id")
        if let token = tokenProvider() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        // The backend is FastAPI/Pydantic, which serializes and expects
        // snake_case field names by default (spoken_answer,
        // proposed_tool_call, available_tools, ...) — without these
        // strategies, every response silently fails to decode (requests
        // can still succeed with a 200 if the mismatched fields happen to
        // have server-side defaults, which is exactly what masked this).
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(body)

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw BackendAPIError.requestFailed(status)
        }
        do {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw BackendAPIError.decodingFailed
        }
    }
}
