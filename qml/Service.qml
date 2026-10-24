pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Side effects only (§5): the tick, the scan, setup, notifications and the last good
// card. Every number and string comes from the engine; a hover never starts a process.
Item {
    id: root

    // Injected by omarchy-shell.
    property var shell: null
    // The code version the widget expects (BarWidget.qml expectedServiceVersion);
    // keepLoaded keeps an old Service running across an update until restart.
    readonly property int serviceVersion: 1

    // The system Python: a mise/pyenv shim on PATH may be older than 3.11.
    readonly property string python: "/usr/bin/python3"
    readonly property string enginePath: root.localPath("../python/disk_space.py")
    readonly property string binPath: root.localPath("../bin/kanso-disk-space")
    function localPath(relative) {
        var u = Qt.resolvedUrl(relative).toString();
        return u.startsWith("file://") ? u.slice(7) : u;
    }
    // A login shell, so the Omarchy commands and the user's PATH are found.
    readonly property var loginShell: ["bash", "-lc", 'exec "$@"', "bash"]

    // An engine verb with the widget settings, and its run: the card it prints
    // is accepted (with its notifications only from a tick).
    function engineCmd(verb) {
        return [root.python, root.enginePath, verb].concat(root.settingArgs());
    }
    component EngineProc: Process {
        id: proc
        property bool sendNotes: false
        signal accepted
        stdout: StdioCollector {
            id: out
        }
        stderr: StdioCollector {
            id: err
        }
        property bool preview: false
        onExited: {
            root.accept(out.text, err.text, proc.sendNotes, proc.preview);
            proc.accepted();
        }
    }

    // The last good card (schema 4) and the last engine failure ("" when fine).
    property var card: null
    property string engineError: ""

    // The widget settings, handed over by the bar widget (the Service has no
    // settings of its own). Until then the engine uses what the last tick
    // stored, else its defaults (D29).
    property var settings: null
    function configure(next) {
        var changed = JSON.stringify(next) !== JSON.stringify(root.settings);
        root.settings = next;
        if (changed)
            settleTimer.restart();
    }

    // The widget hands over its defaults, then its shell.json values a moment
    // later; tick once, on the settled lines.
    Timer {
        id: settleTimer
        interval: 300
        onTriggered: root.start()
    }

    function settingArgs() {
        var s = root.settings;
        if (!s)
            return [];
        return ["--watch", String(s.watch), "--low", String(s.low), "--critical", String(s.critical), "--horizon", String(s.horizon), "--notifications", s.notifications ? "1" : "0", "--number-on-bar", s.numberOnBar ? "1" : "0", "--totals-only", s.totalsOnly ? "1" : "0"];
    }

    // The tick, every 15 minutes: the first waits for the widget's settings, so a
    // start ticks once with the right lines (with no widget, after 10 s).
    function start() {
        firstTickFallback.stop();
        if (tickTimer.running)
            root.tick();
        else
            tickTimer.start();
    }

    Timer {
        id: firstTickFallback
        interval: 10 * 1000
        running: true
        onTriggered: root.start()
    }

    Timer {
        id: tickTimer
        interval: 15 * 60 * 1000
        repeat: true
        triggeredOnStart: true
        onTriggered: root.tick()
    }

    function tick() {
        if (tickProc.running) {
            root.tickQueued = true;
            return;
        }
        tickProc.command = root.engineCmd("tick");
        tickProc.running = true;
    }
    property bool tickQueued: false

    EngineProc {
        id: tickProc
        sendNotes: true
        onAccepted: {
            if (root.tickQueued) {
                root.tickQueued = false;
                Qt.callLater(root.tick);
            }
        }
    }

    // A card replaces the last one only when it parsed and the engine says ok;
    // anything else keeps the last good card and records why.
    function accept(text, err, sendNotes, fromPreview) {
        let doc = null;
        try {
            doc = JSON.parse(text);
        } catch (e) {
            doc = null;
        }
        if (!doc || doc.schema !== 4) {
            root.engineError = (err || "no output").trim().split("\n").pop();
            return;
        }
        if (doc.state === "error") {
            root.engineError = doc.message;
            return;
        }
        root.engineError = "";
        // a preview holds the card until it ends (D130); notes still go out
        if (root.previewing === "" || fromPreview)
            root.card = doc;
        if (sendNotes) {
            root.notify(doc.notify || []);
            root.scanIfDue(doc);
        }
    }

    // The scan: when the engine says one is due (daily, a current helper not yet
    // run, after a cleanup: D94, D95), or on demand.
    readonly property bool scanning: scanProc.running

    function scanIfDue(doc) {
        if (doc.exactness.scan_due)
            root.scan();
    }

    function scan() {
        if (scanProc.running)
            return;
        scanProc.command = root.engineCmd("scan");
        scanProc.running = true;
    }

    EngineProc {
        id: scanProc
        onAccepted: {
            // the first measured scan after setup has landed (D80)
            if (root.setupState === "running")
                root.setupState = "idle";
        }
    }

    // Setup (D80) through bin's setup (the polkit dialog), then a scan.
    // idle · running · cancelled · needs-admin · terminal · failed
    property string setupState: "idle"
    readonly property var setupExits: ({
            "10": "cancelled",
            "11": "needs-admin",
            "12": "terminal"
        })

    function setup() {
        if (setupProc.running || root.setupState === "running")
            return;
        root.setupState = "running";
        setupProc.command = root.loginShell.concat([root.binPath, "setup"]);
        setupProc.running = true;
    }

    // Not now: the panel opens fresh on the next click.
    function resetSetup() {
        if (root.setupState !== "running")
            root.setupState = "idle";
    }

    Process {
        id: setupProc
        onExited: code => {
            if (code !== 0) {
                root.setupState = root.setupExits[String(code)] || "failed";
                return;
            }
            // "Setting up…" stays until the measured scan lands
            if (scanProc.running)
                root.setupState = "idle";
            else
                root.scan();
        }
    }

    // Seen (D58): Cleanup opened, or Ask agent; the engine records the baseline.
    function seen() {
        if (seenProc.running)
            return;
        seenProc.command = root.engineCmd("seen");
        seenProc.running = true;
    }

    EngineProc {
        id: seenProc
    }

    // A visit to the full view (D115): the engine's "since you last looked".
    function viewed() {
        if (viewedProc.running)
            return;
        viewedProc.command = root.engineCmd("viewed");
        viewedProc.running = true;
    }

    EngineProc {
        id: viewedProc
    }

    // The preview mode (D130): a saved fixture state painted in the real card,
    // read from the plugin's tests/fixtures/preview/<name>; nothing is
    // measured or written. "" ends it and ticks for the live card.
    property string previewing: ""
    readonly property string previewRoot: root.localPath("../tests/fixtures/preview")
    function preview(name) {
        // a fixture's folder name, never a path
        if (!/^[a-z0-9-]*$/.test(name))
            return;
        root.previewing = name;
        if (name === "") {
            root.tick();
            return;
        }
        previewProc.command = [root.python, root.enginePath, "preview", root.previewRoot + "/" + name];
        previewProc.running = true;
    }

    EngineProc {
        id: previewProc
        preview: true
    }

    // The engine's notify list: each argv is the whole omarchy-notification-send
    // command.
    function notify(notes) {
        for (let i = 0; i < notes.length; i++)
            root.runArgv(notes[i].argv);
    }

    // An engine-given command (Used's "Explore in dua ›"), or bin's verb.
    function runArgv(argv) {
        Quickshell.execDetached(root.loginShell.concat(argv));
    }

    // bin/kanso-disk-space: ask | copy | save | menu
    function runBin(verb) {
        root.runArgv([root.binPath, verb]);
    }

    function status() {
        return {
            "service_version": root.serviceVersion,
            "engine_error": root.engineError,
            "card_state": root.card ? root.card.state : "none",
            "card_as_of": root.card ? root.card.as_of : 0,
            "settings": root.settings,
            "tick_running": tickProc.running,
            "scanning": scanProc.running,
            "setup_state": root.setupState,
            "scan_at": root.card ? root.card.scan_at : 0,
            "previewing": root.previewing
        };
    }
}
