import Foundation

/// Streams classified gesture events to pc-agent's `/gesture/stream`
/// WebSocket endpoint. First streaming/WebSocket client in this codebase —
/// deliberately minimal: no reconnect loop, no heartbeat. If the connection
/// drops, `send(_:)` just logs and swallows the failure; the caller
/// (`LiveHandGestureController`) surfaces the resulting silence via its own
/// state, and the user says "browse" again to restart.
public final class URLSessionGestureStreamClient: GestureStreamClient, @unchecked Sendable {
    private let baseURL: URL
    private let pairingToken: String
    private let session: URLSession
    private var webSocketTask: URLSessionWebSocketTask?

    public init(baseURL: URL, pairingToken: String, session: URLSession = .init(configuration: .ephemeral)) {
        self.baseURL = baseURL
        self.pairingToken = pairingToken
        self.session = session
    }

    public func connect() async throws {
        var request = URLRequest(url: streamURL())
        request.setValue("Bearer \(pairingToken)", forHTTPHeaderField: "Authorization")
        let task = session.webSocketTask(with: request)
        task.resume()

        // pc-agent rejects a bad/missing pairing token by closing the
        // socket immediately (policy-violation, 1008) right after the
        // handshake. The protocol never sends a hello/ack message, so a
        // short receive() raced against a timeout is how an immediate
        // rejection is distinguished from "connected, just no messages
        // yet" — timing out means the connection is alive and idle.
        let outcome = await withTaskGroup(of: ConnectOutcome.self) { group in
            group.addTask {
                do {
                    _ = try await task.receive()
                    return .unexpectedMessage
                } catch {
                    return .closed
                }
            }
            group.addTask {
                try? await Task.sleep(nanoseconds: 800_000_000)
                return .timedOut
            }
            let first = await group.next() ?? .timedOut
            group.cancelAll()
            return first
        }

        switch outcome {
        case .timedOut, .unexpectedMessage:
            webSocketTask = task
        case .closed:
            if task.closeCode == .policyViolation {
                throw GestureStreamError.unauthorized
            }
            throw GestureStreamError.connectionFailed("Connection closed during handshake (code \(task.closeCode.rawValue))")
        }
    }

    public func send(_ event: GestureEvent) async {
        guard let webSocketTask else { return }
        do {
            let data = try JSONEncoder().encode(event)
            guard let json = String(data: data, encoding: .utf8) else { return }
            try await webSocketTask.send(.string(json))
        } catch {
            NSLog("[JarvisGesture] send failed: \(error)")
        }
    }

    public func disconnect() async {
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        webSocketTask = nil
    }

    private func streamURL() -> URL {
        let httpURL = baseURL.appendingPathComponent("gesture").appendingPathComponent("stream")
        var components = URLComponents(url: httpURL, resolvingAgainstBaseURL: false)!
        components.scheme = (components.scheme == "https") ? "wss" : "ws"
        return components.url!
    }

    private enum ConnectOutcome: Sendable {
        case timedOut
        case unexpectedMessage
        case closed
    }
}
