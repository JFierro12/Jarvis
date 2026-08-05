import SwiftUI
import JarvisKit

struct MemoriesView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss
    @State private var records: [MemoryRecord] = []
    @State private var searchText = ""
    @State private var showingDeleteAllConfirmation = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(records) { record in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(record.title).font(.headline)
                        Text(record.normalizedSummary).font(.subheadline).foregroundStyle(.secondary)
                        Text(record.timestamp, style: .relative).font(.caption2).foregroundStyle(.tertiary)
                    }
                    .swipeActions {
                        Button("Delete", role: .destructive) {
                            Task {
                                try? await environment.memoryRepository.delete(id: record.id)
                                await reload()
                            }
                        }
                    }
                }
            }
            .searchable(text: $searchText)
            .onSubmit(of: .search) { Task { await search() } }
            .onChange(of: searchText) { _, newValue in
                if newValue.isEmpty { Task { await reload() } }
            }
            .navigationTitle("Memories")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .destructiveAction) {
                    Button("Delete All", role: .destructive) { showingDeleteAllConfirmation = true }
                }
            }
            .confirmationDialog("Delete all memories? This cannot be undone.", isPresented: $showingDeleteAllConfirmation, titleVisibility: .visible) {
                Button("Delete All", role: .destructive) {
                    Task {
                        try? await environment.memoryRepository.deleteAll()
                        await reload()
                    }
                }
            }
            .task { await reload() }
            .overlay {
                if records.isEmpty {
                    Text("No memories yet.").foregroundStyle(.secondary)
                }
            }
        }
    }

    private func reload() async {
        records = (try? await environment.memoryRepository.all()) ?? []
    }

    private func search() async {
        let results = (try? await environment.memoryRepository.search(MemoryQuery(text: searchText))) ?? []
        records = results.map { $0.record }
    }
}
