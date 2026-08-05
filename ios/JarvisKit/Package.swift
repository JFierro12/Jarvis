// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "JarvisKit",
    platforms: [.iOS(.v17)],
    products: [
        .library(name: "JarvisKit", targets: ["JarvisKit"])
    ],
    dependencies: [
        .package(url: "https://github.com/facebook/meta-wearables-dat-ios", from: "0.9.0")
    ],
    targets: [
        .target(
            name: "JarvisKit",
            dependencies: [
                .product(name: "MWDATCore", package: "meta-wearables-dat-ios"),
                .product(name: "MWDATCamera", package: "meta-wearables-dat-ios"),
                .product(name: "MWDATMockDevice", package: "meta-wearables-dat-ios")
            ]
        ),
        .testTarget(
            name: "JarvisKitTests",
            dependencies: ["JarvisKit"]
        )
    ]
)
