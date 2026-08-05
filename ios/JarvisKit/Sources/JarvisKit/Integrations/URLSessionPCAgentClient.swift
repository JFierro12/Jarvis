import Foundation

/// Talks to the local pc-agent service (see pc-agent/). Bound to localhost
/// or the local network by default; requires a pairing token. Only ever
/// sends predefined command IDs, never a raw command string.
public final class URLSessionPCAgentClient: PCAgentClient, @unchecked Sendable {
    private let baseURL: URL
    private let pairingToken: String
    private let session: URLSession

    public init(baseURL: URL, pairingToken: String, session: URLSession = .init(configuration: .ephemeral)) {
        self.baseURL = baseURL
        self.pairingToken = pairingToken
        self.session = session
    }

    public func status() async throws -> PCStatus {
        var request = URLRequest(url: baseURL.appendingPathComponent("/status"))
        request.setValue("Bearer \(pairingToken)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 5
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw PCAgentError.offline }
            return try JSONDecoder().decode(PCStatus.self, from: data)
        } catch is DecodingError {
            throw PCAgentError.offline
        } catch let error as PCAgentError {
            throw error
        } catch {
            throw PCAgentError.offline
        }
    }

    public func runCommand(_ id: PCCommandID) async throws -> ToolResult {
        var request = URLRequest(url: baseURL.appendingPathComponent("/commands/\(id.rawValue)"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(pairingToken)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 10
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw PCAgentError.offline }
        if http.statusCode == 401 { throw PCAgentError.unauthorized }
        if http.statusCode == 429 { throw PCAgentError.rateLimited }
        guard http.statusCode == 200 else { throw PCAgentError.offline }
        return ToolResult(success: true, output: "\(id.rawValue) completed", confirmedAction: true)
    }
}
