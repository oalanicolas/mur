import AppKit
import CryptoKit
import Sparkle

@MainActor
final class FeedProbe: NSObject, NSApplicationDelegate, SPUUpdaterDelegate {
    var updater: SPUUpdater!
    var userDriver: SPUStandardUserDriver!
    let host: Bundle
    init(path: String) { host = Bundle(path: path)!; super.init() }
    func applicationDidFinishLaunching(_ notification: Notification) {
        userDriver = SPUStandardUserDriver(hostBundle: host, delegate: nil)
        updater = SPUUpdater(hostBundle: host, applicationBundle: host, userDriver: userDriver, delegate: self)
        do { try updater.start(); updater.checkForUpdateInformation() }
        catch { finish("error", error.localizedDescription) }
    }
    func finish(_ status: String, _ detail: String) {
        let data = try! JSONSerialization.data(withJSONObject: ["status":status,"detail":detail])
        print(String(data: data, encoding: .utf8)!)
        fflush(stdout)
        exit(status == "error" ? 1 : 0)
    }
    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) { finish("update-available",item.versionString) }
    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: Error) { finish("up-to-date",host.object(forInfoDictionaryKey:"CFBundleVersion") as? String ?? "") }
    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) { finish("error",error.localizedDescription) }
}

@MainActor
final class UpdateValidation: NSObject, NSApplicationDelegate, SPUUserDriver, SPUUpdaterDelegate {
    var updater: SPUUpdater!
    let output = URL(fileURLWithPath: Bundle.main.object(forInfoDictionaryKey: "MURValidationResult") as! String)

    func record(_ phase: String, _ detail: String = "") {
        let data = try! JSONSerialization.data(withJSONObject: ["phase":phase,"detail":detail])
        try! data.write(to: output, options: .atomic)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String == "3.0.3" {
            record("relaunched")
            NSApp.terminate(nil)
            return
        }
        updater = SPUUpdater(hostBundle: Bundle.main, applicationBundle: Bundle.main, userDriver: self, delegate: self)
        do {
            try updater.start()
            updater.checkForUpdates()
        } catch { record("error", error.localizedDescription); NSApp.terminate(nil) }
    }

    func show(_ request: SPUUpdatePermissionRequest, reply: @escaping (SUUpdatePermissionResponse) -> Void) {
        reply(SUUpdatePermissionResponse(automaticUpdateChecks: false, sendSystemProfile: false))
    }
    func showUserInitiatedUpdateCheck(cancellation: @escaping () -> Void) { record("checking") }
    func showUpdateFound(with appcastItem: SUAppcastItem, state: SPUUserUpdateState, reply: @escaping (SPUUserUpdateChoice) -> Void) {
        record("found", appcastItem.versionString)
        reply(.install)
    }
    func showUpdateReleaseNotes(with downloadData: SPUDownloadData) {}
    func showUpdateReleaseNotesFailedToDownloadWithError(_ error: Error) {}
    func showUpdateNotFoundWithError(_ error: Error, acknowledgement: @escaping () -> Void) {
        record("not-found", error.localizedDescription); acknowledgement(); NSApp.terminate(nil)
    }
    func showUpdaterError(_ error: Error, acknowledgement: @escaping () -> Void) {
        record("error", error.localizedDescription); acknowledgement(); NSApp.terminate(nil)
    }
    func showDownloadInitiated(cancellation: @escaping () -> Void) { record("downloading") }
    func showDownloadDidReceiveExpectedContentLength(_ expectedContentLength: UInt64) {}
    func showDownloadDidReceiveData(ofLength length: UInt64) {}
    func showDownloadDidStartExtractingUpdate() { record("extracting") }
    func showExtractionReceivedProgress(_ progress: Double) {}
    func showReady(toInstallAndRelaunch reply: @escaping (SPUUserUpdateChoice) -> Void) { record("ready"); reply(.install) }
    func showInstallingUpdate(withApplicationTerminated applicationTerminated: Bool, retryTerminatingApplication: @escaping () -> Void) { record("installing") }
    func showUpdateInstalledAndRelaunched(_ relaunched: Bool, acknowledgement: @escaping () -> Void) { acknowledgement() }
    func dismissUpdateInstallation() {}
}

@main
struct ValidateUpdates {
    @MainActor static func main() throws {
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "probe-feed" {
            let app = NSApplication.shared
            let delegate = FeedProbe(path: CommandLine.arguments[2])
            app.delegate = delegate
            withExtendedLifetime(delegate) { app.run() }
            return
        }
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "public-key" {
            let seed = Data(base64Encoded: try String(contentsOfFile: CommandLine.arguments[2], encoding: .utf8))!
            print(try Curve25519.Signing.PrivateKey(rawRepresentation: seed).publicKey.rawRepresentation.base64EncodedString())
            return
        }
        let app = NSApplication.shared
        let delegate = UpdateValidation()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
