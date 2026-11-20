import SwiftUI

@main
struct MangaBrainApp: App {
    @State private var app = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    init() {
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(Theme.surface)
        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some Scene {
        WindowGroup {
            MainTabView()
                .environment(app)
                .environment(app.filters)
                .environment(app.discover)
                .tint(Theme.accent)
                .preferredColorScheme(.dark)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { app.filters.save() }
        }
    }
}

struct MainTabView: View {
    @Environment(AppModel.self) private var app
    @Environment(Filters.self) private var filters

    var body: some View {
        @Bindable var app = app
        TabView(selection: $app.tab) {
            DiscoverView()
                .tabItem { Label("Discover", systemImage: "safari") }
                .tag(AppTab.discover)
            FiltersView()
                .tabItem { Label("Filters", systemImage: "slider.horizontal.3") }
                .badge(filters.activeCount)
                .tag(AppTab.filters)
            MixView()
                .tabItem { Label("Mix", systemImage: "shuffle") }
                .badge(app.mix.count)
                .tag(AppTab.mix)
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
                .tag(AppTab.settings)
        }
        .task(id: app.generation) {
            await app.refreshSettings()
            #if DEBUG
            await app.runScreenshotHooks()
            #endif
        }
    }
}
