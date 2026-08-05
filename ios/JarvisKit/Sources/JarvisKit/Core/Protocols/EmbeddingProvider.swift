import Foundation

public protocol EmbeddingProvider: AnyObject, Sendable {
    func embed(_ text: String) async throws -> [Float]
}
