import Foundation

/// Enrollment codes and approval credentials must never follow redirects.
private final class RuntimeRedirectGuard: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

public struct RuntimeCredentials: Codable, Sendable {
    public let contractVersion: String
    public let accessToken: String
    public let approvalKey: String
    public let sessionId: String
    public let deviceId: String
    public let expiresInSeconds: Int
}

public struct RuntimeCapability: Codable, Sendable {
    public let name: String
    public let description: String
    public let riskLevel: String
    public let requiresConfirmation: Bool
    public let requiredPermissions: [String]
    public let targetDevice: String
}

public struct RuntimeConversation: Codable, Sendable, Identifiable {
    public let id: String
    public let provider: String
    public let model: String
    public let promptVersion: String
}

public actor SharedRuntimeClient: SharedAssistantRuntime {
    private let baseURL: URL
    private let session: URLSession
    private let tokenProvider: @Sendable () -> String?
    private let approvalProvider: @Sendable () -> String?
    private let conversationProvider: @Sendable () -> String
    private let saveConversation: @Sendable (String) -> Void

    public init(baseURL: URL, session: URLSession = .init(configuration: .ephemeral),
                tokenProvider: @escaping @Sendable () -> String?,
                approvalProvider: @escaping @Sendable () -> String?,
                conversationProvider: @escaping @Sendable () -> String,
                saveConversation: @escaping @Sendable (String) -> Void) {
        self.baseURL = baseURL
        self.session = session
        self.tokenProvider = tokenProvider
        self.approvalProvider = approvalProvider
        self.conversationProvider = conversationProvider
        self.saveConversation = saveConversation
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET",
                                       body: [String: String]? = nil, approval: Bool = false,
                                       authenticated: Bool = true) async throws -> T {
        // Phone pairing never downgrades to plaintext LAN HTTP.
        guard baseURL.scheme == "https" else { throw BackendAPIError.notConfigured }
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 60
        if authenticated {
            guard let token = tokenProvider(), !token.isEmpty else { throw BackendAPIError.notConfigured }
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if approval {
            guard let key = approvalProvider(), !key.isEmpty else { throw BackendAPIError.notConfigured }
            request.setValue(key, forHTTPHeaderField: "X-Jarvis-Approval-Key")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: request, delegate: RuntimeRedirectGuard())
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw BackendAPIError.requestFailed((response as? HTTPURLResponse)?.statusCode ?? -1)
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: data)
    }

    public func enroll(code: String, name: String) async throws -> RuntimeCredentials {
        try await request("/v2/devices/enroll", method: "POST", body: ["code": code, "name": name], authenticated: false)
    }

    public func capabilities() async throws -> [RuntimeCapability] {
        try await request("/v2/capabilities")
    }

    public func conversations() async throws -> [RuntimeConversation] {
        struct Envelope: Decodable { let conversations: [RuntimeConversation] }
        let response: Envelope = try await request("/v2/conversations")
        return response.conversations
    }

    public func turn(question: String) async throws -> SharedTurn {
        let response: SharedTurn = try await request("/v2/turns", method: "POST", body: [
            "conversation_id": conversationProvider(), "turn_id": UUID().uuidString,
            "question": question, "target_device": "desktop"
        ])
        guard response.contractVersion == "2" else { throw BackendAPIError.decodingFailed }
        saveConversation(response.conversationId)
        return response
    }

    public func execute(actionId: String) async throws -> ToolResult {
        struct Result: Decodable { let success: Bool; let output: String }
        let response: Result = try await request("/v1/tools/execute", method: "POST", body: ["action_id": actionId])
        return ToolResult(success: response.success, output: response.output)
    }

    private struct ActionState: Decodable { let actionId: String; let state: String }

    public func approve(actionId: String) async throws {
        let _: ActionState = try await request("/v1/actions/\(actionId)/approve", method: "POST", approval: true)
    }

    public func cancel(actionId: String) async throws {
        let _: ActionState = try await request("/v1/actions/\(actionId)/cancel", method: "POST")
    }
}

/// Live fallback fails visibly instead of simulating a successful device action.
public final class UnavailableToolExecutor: ToolExecutor, Sendable {
    public init() {}
    public func execute(_ call: ToolCall) async throws -> ToolResult {
        ToolResult(success: false, output: "Connect the shared runtime to use this capability.")
    }
}

public final class UnavailableLanguageProvider: LanguageReasoningProvider, Sendable {
    public init() {}
    public func reason(_ request: ReasoningRequest) async throws -> ReasoningResponse {
        throw LanguageReasoningError.unavailable
    }
}

public final class UnavailableVisionProvider: VisionReasoningProvider, Sendable {
    public init() {}
    public func analyze(image: CapturedImage, question: String) async throws -> VisionAnalysisResult {
        throw VisionReasoningError.unavailable
    }
}
