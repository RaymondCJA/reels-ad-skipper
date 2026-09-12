import AVFoundation
import CoreImage
import CoreMediaIO
import Foundation

private func log(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

private func enableIOSScreenCaptureDevices() throws {
    var address = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain)
    )
    var allowed: UInt32 = 1
    let status = CMIOObjectSetPropertyData(
        CMIOObjectID(kCMIOObjectSystemObject),
        &address,
        0,
        nil,
        UInt32(MemoryLayout<UInt32>.size),
        &allowed
    )
    guard status == noErr else {
        throw NSError(
            domain: NSOSStatusErrorDomain,
            code: Int(status),
            userInfo: [NSLocalizedDescriptionKey: "Could not enable iOS screen-capture devices"]
        )
    }
}

private func requestCameraAccess() -> Bool {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized:
        return true
    case .notDetermined:
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .video) { value in
            granted = value
            semaphore.signal()
        }
        semaphore.wait()
        return granted
    default:
        return false
    }
}

private func discoverIPhone(timeout: TimeInterval) -> AVCaptureDevice? {
    let deadline = Date().addingTimeInterval(timeout)
    repeat {
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.external],
            mediaType: nil,
            position: .unspecified
        )
        if let device = discovery.devices.first(where: { $0.hasMediaType(.muxed) }) {
            return device
        }
        RunLoop.current.run(until: Date().addingTimeInterval(0.25))
    } while Date() < deadline
    return nil
}

private struct Options {
    var width = 450
    var height = 970
    var fps = 4.0
    var listOnly = false

    static func parse() throws -> Options {
        var result = Options()
        var index = 1
        let args = CommandLine.arguments
        while index < args.count {
            switch args[index] {
            case "--width":
                index += 1
                guard index < args.count, let value = Int(args[index]), value > 0 else {
                    throw NSError(domain: "iphone-capture", code: 2,
                                  userInfo: [NSLocalizedDescriptionKey: "Invalid --width"])
                }
                result.width = value
            case "--height":
                index += 1
                guard index < args.count, let value = Int(args[index]), value > 0 else {
                    throw NSError(domain: "iphone-capture", code: 2,
                                  userInfo: [NSLocalizedDescriptionKey: "Invalid --height"])
                }
                result.height = value
            case "--fps":
                index += 1
                guard index < args.count, let value = Double(args[index]), value > 0 else {
                    throw NSError(domain: "iphone-capture", code: 2,
                                  userInfo: [NSLocalizedDescriptionKey: "Invalid --fps"])
                }
                result.fps = value
            case "--list":
                result.listOnly = true
            default:
                throw NSError(domain: "iphone-capture", code: 2,
                              userInfo: [NSLocalizedDescriptionKey: "Unknown option \(args[index])"])
            }
            index += 1
        }
        return result
    }
}

private final class FrameWriter: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate,
                                 @unchecked Sendable {
    private let output = FileHandle.standardOutput
    private let context = CIContext(options: [.cacheIntermediates: false])
    private let width: Int
    private let height: Int
    private let interval: TimeInterval
    private var lastFrameTime = 0.0
    private var announcedResolution = false

    init(width: Int, height: Int, fps: Double) {
        self.width = width
        self.height = height
        self.interval = 1.0 / fps
    }

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        let now = ProcessInfo.processInfo.systemUptime
        guard now - lastFrameTime >= interval,
              let source = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        lastFrameTime = now

        let sourceWidth = CVPixelBufferGetWidth(source)
        let sourceHeight = CVPixelBufferGetHeight(source)
        if !announcedResolution {
            log("Capturing \(sourceWidth)x\(sourceHeight), output \(width)x\(height)")
            announcedResolution = true
        }

        let image = CIImage(cvPixelBuffer: source)
        let scaleX = CGFloat(width) / image.extent.width
        let scaleY = CGFloat(height) / image.extent.height
        let scaled = image.transformed(by: CGAffineTransform(scaleX: scaleX, y: scaleY))
        let rowBytes = width * 4
        var pixels = Data(count: rowBytes * height)
        pixels.withUnsafeMutableBytes { storage in
            guard let base = storage.baseAddress else { return }
            context.render(
                scaled,
                toBitmap: base,
                rowBytes: rowBytes,
                bounds: CGRect(x: 0, y: 0, width: width, height: height),
                format: .BGRA8,
                colorSpace: CGColorSpaceCreateDeviceRGB()
            )
        }

        var packet = Data("RLS1".utf8)
        for value in [UInt32(width), UInt32(height), UInt32(pixels.count)] {
            var bigEndian = value.bigEndian
            withUnsafeBytes(of: &bigEndian) { packet.append(contentsOf: $0) }
        }
        packet.append(pixels)
        do {
            try self.output.write(contentsOf: packet)
        } catch {
            log("Output pipe closed: \(error.localizedDescription)")
            exit(0)
        }
    }
}

do {
    let options = try Options.parse()
    try enableIOSScreenCaptureDevices()

    if options.listOnly {
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.external], mediaType: nil, position: .unspecified
        )
        for device in discovery.devices {
            print("\(device.localizedName)\tmuxed=\(device.hasMediaType(.muxed))\t\(device.uniqueID)")
        }
        exit(0)
    }

    guard requestCameraAccess() else {
        throw NSError(
            domain: "iphone-capture",
            code: 3,
            userInfo: [NSLocalizedDescriptionKey:
                "Camera permission denied. Enable it for the launching app in System Settings > Privacy & Security > Camera."]
        )
    }
    guard let device = discoverIPhone(timeout: 15) else {
        throw NSError(
            domain: "iphone-capture",
            code: 4,
            userInfo: [NSLocalizedDescriptionKey:
                "No USB iPhone screen device found. Unlock the iPhone, trust this Mac, and close QuickTime."]
        )
    }
    log("Using \(device.localizedName)")

    let session = AVCaptureSession()
    session.sessionPreset = .high
    session.beginConfiguration()
    let input = try AVCaptureDeviceInput(device: device)
    guard session.canAddInput(input) else {
        throw NSError(domain: "iphone-capture", code: 5,
                      userInfo: [NSLocalizedDescriptionKey: "Cannot add iPhone capture input"])
    }
    session.addInput(input)

    let videoOutput = AVCaptureVideoDataOutput()
    videoOutput.videoSettings = [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
    ]
    videoOutput.alwaysDiscardsLateVideoFrames = true
    let writer = FrameWriter(width: options.width, height: options.height, fps: options.fps)
    let captureQueue = DispatchQueue(label: "reels-skipper.iphone-capture")
    videoOutput.setSampleBufferDelegate(writer, queue: captureQueue)
    guard session.canAddOutput(videoOutput) else {
        throw NSError(domain: "iphone-capture", code: 6,
                      userInfo: [NSLocalizedDescriptionKey: "Cannot add video output"])
    }
    session.addOutput(videoOutput)

    // A wired iPhone screen-capture session routes the phone's program audio to
    // the Mac. Preview it through the Mac's currently selected sound output so
    // capture does not make Reels appear silent.
    let audioPreview = AVCaptureAudioPreviewOutput()
    audioPreview.volume = 1.0
    if session.canAddOutput(audioPreview) {
        session.addOutput(audioPreview)
        log("Audio monitoring enabled through the Mac")
    } else {
        log("WARNING: iPhone audio monitoring is unavailable")
    }

    session.commitConfiguration()
    session.startRunning()
    log("Capture started")
    RunLoop.main.run()
} catch {
    log("ERROR: \(error.localizedDescription)")
    exit(1)
}
