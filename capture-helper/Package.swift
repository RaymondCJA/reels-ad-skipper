// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "iPhoneCapture",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "iphone-capture",
            linkerSettings: [
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreImage"),
                .linkedFramework("CoreMediaIO"),
            ]
        )
    ]
)
