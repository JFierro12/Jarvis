import XCTest
@testable import JarvisKit

final class MemoryRepositoryTests: XCTestCase {
    func testSaveAndSearchByKeyword() async throws {
        let repo = InMemoryMemoryRepository()
        let record = MemoryRecord(type: .objectLocation, title: "keys", originalUserText: "remember where I put my keys", normalizedSummary: "Keys placed on the kitchen counter", tags: ["keys"])
        try await repo.save(record)

        let results = try await repo.search(MemoryQuery(text: "keys"))
        XCTAssertEqual(results.first?.record.id, record.id)
    }

    func testDeletedMemoryIsExcludedFromSearch() async throws {
        let repo = InMemoryMemoryRepository()
        let record = MemoryRecord(type: .objectLocation, title: "AirPods", originalUserText: "where did I last see my AirPods", normalizedSummary: "AirPods on the nightstand", tags: ["airpods"])
        try await repo.save(record)
        try await repo.delete(id: record.id)

        let results = try await repo.search(MemoryQuery(text: "airpods"))
        XCTAssertTrue(results.isEmpty)
    }

    func testDeleteAllClearsEverything() async throws {
        let repo = InMemoryMemoryRepository()
        try await repo.save(MemoryRecord(type: .note, title: "a", originalUserText: "a", normalizedSummary: "a"))
        try await repo.save(MemoryRecord(type: .note, title: "b", originalUserText: "b", normalizedSummary: "b"))
        try await repo.deleteAll()

        let all = try await repo.all()
        XCTAssertTrue(all.isEmpty)
    }

    func testSearchNeverClaimsObjectIsStillThereJustLastRecorded() async throws {
        // This is a product-behavior test: search results carry a timestamp,
        // not a live "current location" claim — the caller is responsible
        // for phrasing it as "last recorded", but the data must support that.
        let repo = InMemoryMemoryRepository()
        let past = Date().addingTimeInterval(-3600)
        var record = MemoryRecord(type: .objectLocation, title: "wallet", originalUserText: "remember where I left my wallet", normalizedSummary: "Wallet on the hallway table")
        record.timestamp = past
        try await repo.save(record)

        let results = try await repo.search(MemoryQuery(text: "wallet"))
        XCTAssertEqual(results.first?.record.timestamp, past)
    }
}
