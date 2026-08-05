import Foundation

/// Deterministic bag-of-words hash embedding — good enough to exercise
/// semantic-search code paths in tests without a real model.
public final class MockEmbeddingProvider: EmbeddingProvider, @unchecked Sendable {
    private let dimensions: Int

    public init(dimensions: Int = 32) {
        self.dimensions = dimensions
    }

    public func embed(_ text: String) async throws -> [Float] {
        var vector = [Float](repeating: 0, count: dimensions)
        for word in text.lowercased().split(separator: " ") {
            let index = abs(word.hashValue) % dimensions
            vector[index] += 1
        }
        let magnitude = sqrt(vector.reduce(0) { $0 + $1 * $1 })
        guard magnitude > 0 else { return vector }
        return vector.map { $0 / magnitude }
    }
}
