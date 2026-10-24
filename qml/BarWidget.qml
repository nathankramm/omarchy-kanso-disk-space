pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import qs.Ui
import qs.Commons
import "../js/Model.js" as Model

// Bar button (D59): the engine's glyph, colour and "45G"; never a process on hover.
// Hover: the Glance; left-click: the full view; right-click: the menu (D40).
BarWidget {
    id: root
    moduleName: "io.github.nathankramm.kanso-disk-space"

    readonly property var service: bar && bar.shell ? bar.shell.serviceFor("io.github.nathankramm.kanso-disk-space") : null
    readonly property int expectedServiceVersion: 1
    readonly property var card: service ? service.card : null
    readonly property var face: card ? card.bar : null
    readonly property string glyph: face ? face.glyph : "\uf0a0"
    // A vertical bar shows the glyph only; the number lives in the card.
    readonly property string number: face && !root.vertical ? face.text : ""
    // Once the Service has stalled the glyph dims, Omarchy's "inactive" (D67),
    // and the cards say since when: the engine names the colour; this compares
    // its epoch with the one clock, refreshed every 30 s and when a card opens.
    property real nowSec: Date.now() / 1000
    readonly property bool stalled: card !== null && card.glance && card.glance.stale_after > 0 && nowSec > card.glance.stale_after
    readonly property color tint: Model.colourOf(face ? (stalled ? face.stale_colour : face.colour) : "muted", Color, card ? card.palette : null)
    // Before the exact measure is set up only the setup panel (D80); where
    // nothing more is exact, only Trend (D77)
    readonly property var exactness: root.card && root.card.exactness ? root.card.exactness : null

    Timer {
        interval: 30 * 1000
        repeat: true
        running: true
        onTriggered: root.nowSec = Date.now() / 1000
    }

    // Both cards: the same HoverCard in a popup under the button.
    component CardPopup: PopupCard {
        id: popup
        property alias card: hcard
        signal actionRequested(string verb, var argv)
        signal escapePressed
        anchorItem: button
        bar: root.bar
        // contentWidth/contentHeight are the WHOLE popup, padding and border
        // included, so the insets are added here: 440 on screen.
        contentWidth: popup.fittedContentWidth(hcard.implicitWidth + popup.padding * 2 + Border.left(popup.borderSpec) + Border.right(popup.borderSpec))
        contentHeight: popup.fittedContentHeight(hcard.implicitHeight)
        onOpenChanged: {
            if (popup.open)
                root.nowSec = Date.now() / 1000;
        }

        HoverCard {
            id: hcard
            doc: root.card
            fallbackText: root.fallbackText
            foreground: Color.popups.text
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
            scanning: root.service ? root.service.scanning === true : false
            setupState: root.service ? root.service.setupState || "idle" : "idle"
            nowSec: root.nowSec
            stalled: root.stalled
            exactness: root.exactness
            Keys.onEscapePressed: popup.escapePressed()
            onActionRequested: (verb, argv) => popup.actionRequested(verb, argv)
        }
    }

    // The widget settings (manifest barWidget.schema), handed to Service,
    // which passes them to every tick.
    readonly property var wanted: ({
            "watch": root.setting("watchGiB", 50),
            "low": root.setting("lowGiB", 30),
            "critical": root.setting("criticalGiB", 15),
            "horizon": root.setting("slowHorizonDays", 14),
            "notifications": root.setting("notifications", true),
            "numberOnBar": root.setting("numberOnBar", true),
            "totalsOnly": root.setting("totalsOnly", false)
        })
    onWantedChanged: root.handOver()
    onServiceChanged: root.handOver()
    function handOver() {
        if (root.service && typeof root.service.configure === "function")
            root.service.configure(root.wanted);
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    // The quick view has no focus grab, so this widget owns its life: open after
    // 250 ms hovered, close 300 ms after button and card are both left, or at
    // once when another owner takes the popout (Kanso Screen Time's timings).
    readonly property bool hoverWanted: button.tooltipHovered === true || quickPopup.containsMouse
    property string hoverState: "closed"       // closed | opening | open | closing | forced

    onHoverWantedChanged: {
        if (hoverWanted) {
            closeTimer.stop();
            if (!quickPopup.open) {
                root.hoverState = "opening";
                openTimer.restart();
            } else {
                root.hoverState = "open";
            }
        } else {
            openTimer.stop();
            if (quickPopup.open) {
                root.hoverState = "closing";
                closeTimer.restart();
            } else {
                root.hoverState = "closed";
            }
        }
    }

    function openQuickView() {
        closeTimer.stop();
        quickPopup.open = true;
        root.hoverState = "forced";
    }

    function closeQuickView() {
        openTimer.stop();
        closeTimer.stop();
        quickPopup.open = false;
        root.hoverState = "closed";
    }

    Timer {
        id: openTimer
        interval: 250
        onTriggered: {
            // no quick view over the full view
            if (root.hoverWanted && !fullPopup.open) {
                quickPopup.open = true;
                root.hoverState = quickPopup.open ? "open" : "closed";
            }
        }
    }

    Timer {
        id: closeTimer
        interval: 300
        onTriggered: {
            if (!root.hoverWanted)
                root.closeQuickView();
        }
    }

    Connections {
        target: root.bar
        ignoreUnknownSignals: true
        function onActivePopoutChanged() {
            // Bar.requestPopout closes the old owner (whose release blanks
            // activePopout) before naming the new one: null here is that
            // hand-off passing through, not a takeover.
            var active = root.bar ? root.bar.activePopout : null;
            if (quickPopup.open && active && active !== quickPopup.coordinatorKey)
                root.closeQuickView();
        }
    }

    CardPopup {
        id: quickPopup
        triggerMode: "hover"
        onOpenChanged: {
            if (!open && root.hoverState !== "closed") {
                openTimer.stop();
                closeTimer.stop();
                root.hoverState = "closed";
            }
        }
    }

    // The full view (D40) opens on Usage and takes focus, so an outside click,
    // Escape or a second click closes it.
    function openFullView(page) {
        root.closeQuickView();
        fullPopup.card.page = root.entryPage(page);
        // a cancelled or refused setup reads fresh on the next click
        if (fullPopup.card.page === "setup" && root.service)
            root.service.resetSetup();
        fullPopup.open = true;
        fullPopup.card.forceActiveFocus();
        root.noteSeen();
        if (root.service)
            root.service.viewed();
    }

    // Reclaim shown in the full view counts as seen (D58): the engine records
    // the reclaimable now, which clears the review condition.
    function noteSeen() {
        if (fullPopup.open && fullPopup.card.page === "reclaim" && root.service)
            root.service.seen();
    }

    function closeFullView() {
        fullPopup.open = false;
    }

    // The setup panel, Trend alone, else the page asked for.
    function entryPage(page) {
        if (root.exactness && root.exactness.panel)
            return "setup";
        if (root.exactness && root.exactness.trend_only)
            return "trend";
        return page || "used";
    }

    // The first measured scan after setup lands: the panel gives way to Usage
    // with its exact numbers.
    onExactnessChanged: {
        if (fullPopup.open && fullPopup.card.page === "setup" && !(root.exactness && root.exactness.panel))
            fullPopup.card.page = root.entryPage("used");
    }

    // The engine's page for a click: Cleanup at Low and worse (D117), or the
    // first warning's What now panel (D131).
    function toggleFullView() {
        if (fullPopup.open) {
            root.closeFullView();
            return;
        }
        root.openFullView(root.card && root.card.bar.click_page ? root.card.bar.click_page : "used");
        var panel = root.card && root.card.bar.click_panel ? root.card.bar.click_panel : "";
        if (panel !== "" && fullPopup.card.page !== "setup")
            fullPopup.card.follow({
                "verb": "panel",
                "panel": panel,
                "argv": []
            });
    }

    CardPopup {
        id: fullPopup
        triggerMode: "click"
        card.full: true
        card.focus: true
        onEscapePressed: root.closeFullView()
        // a page's footer action or a warning's link (Ask agent, Explore in
        // dua, Rescan, Copy): the engine's verb, and its command when it
        // gives one (D94, D119)
        onActionRequested: (verb, argv) => {
            if (!root.service)
                return;
            // Copy keeps the card open; the step reads "Copied" (D120)
            if (verb === "copy") {
                root.service.runArgv(argv);
                return;
            }
            // Rescan and Scan now keep the card open on "Measuring…"
            if (verb === "rescan" || verb === "scan") {
                root.service.scan();
                return;
            }
            // the setup panel (D80): Set up keeps it open on "Setting up…";
            // Not now closes it, and it opens fresh on the next click
            if (verb === "setup") {
                root.service.setup();
                return;
            }
            if (verb === "not-now") {
                root.service.resetSetup();
                root.closeFullView();
                return;
            }
            root.closeFullView();
            if (argv.length > 0)
                root.service.runArgv(argv);
            else
                root.service.runBin(verb);
        }
    }

    Connections {
        target: fullPopup.card
        function onPageChanged() {
            root.noteSeen();
        }
    }

    // Static UI text, shown only while there is no good card yet.
    readonly property string fallbackText: {
        if (!root.service)
            return "Disk space is not running";
        if (root.service.engineError !== "")
            return "Disk space: " + root.service.engineError;
        return "Disk space is starting";
    }

    //   omarchy-shell io.github.nathankramm.kanso-disk-space open|close   # the quick view (screenshot aid)
    //   omarchy-shell io.github.nathankramm.kanso-disk-space full trend   # the full view on a page
    //   omarchy-shell io.github.nathankramm.kanso-disk-space status | jq
    //   omarchy-shell io.github.nathankramm.kanso-disk-space preview low  # a fixture's card; preview "" ends it
    IpcHandler {
        target: "io.github.nathankramm.kanso-disk-space"
        function open(): void {
            root.openQuickView();
        }
        function close(): void {
            root.closeQuickView();
            root.closeFullView();
        }
        // the full view on a page: used | reclaim | trend (the setup panel
        // or Trend alone where the card is not exact, as a click opens it)
        function full(page: string): void {
            root.openFullView(page);
        }
        // a What now panel, as its link opens it (D120; a screenshot aid)
        function panel(id: string): void {
            if (!fullPopup.open)
                root.openFullView(root.card && root.card.bar.click_page ? root.card.bar.click_page : "used");
            fullPopup.card.follow({
                "verb": "panel",
                "panel": id,
                "argv": []
            });
        }
        // the preview mode (D130): a saved fixture state's card in the real
        // card (tests/fixtures/preview/<name>); "" goes back to the live card
        function preview(name: string): void {
            if (root.service)
                root.service.preview(name);
        }
        // the setup panel's Set up, as clicked (D80)
        function setup(): void {
            if (root.service && fullPopup.open && fullPopup.card.page === "setup")
                root.service.setup();
        }
        function status(): string {
            var out = root.service ? root.service.status() : {
                "service": "not running"
            };
            out.hover_state = root.hoverState;
            out.quick_open = quickPopup.open;
            out.full_open = fullPopup.open;
            out.page = fullPopup.card.page;
            out.chart_rect = fullPopup.open && fullPopup.card.page === "trend" ? fullPopup.card.chartRect() : null;
            var b = button.mapToGlobal(button.width / 2, button.height / 2);
            out.button_center = {
                "x": b.x,
                "y": b.y
            };
            out.widget_expects_service = root.expectedServiceVersion;
            return JSON.stringify(out);
        }
    }

    WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        // The glyph alone takes the stock icon path, so it lines up with every
        // other bar icon. With a number the button paints glyph and number
        // itself, in a slot sized to both.
        text: root.glyph
        labelVisible: root.number === ""
        foreground: root.tint
        fixedWidth: root.number === "" ? -1 : Style.bar.iconSlot + numberLabel.implicitWidth + button.scaledHorizontalMargin
        fixedHeight: root.vertical ? Style.bar.iconSlot : -1
        tooltipText: ""
        // Left: the full view; right: the menu (Ask agent · Copy · Save).
        onPressed: function (b) {
            if (b === Qt.LeftButton)
                root.toggleFullView();
            else if (b === Qt.RightButton && root.service)
                root.service.runBin("menu");
        }

        Item {
            id: glyphBox
            width: Style.bar.iconCanvas
            height: Style.bar.iconCanvas
            x: root.number === "" ? (button.width - width) / 2 : (Style.bar.iconSlot - width) / 2
            y: (button.height - height) / 2

            OpticalGlyph {
                visible: root.number !== ""
                anchors.fill: parent
                text: root.glyph
                fontFamily: button.fontFamily
                fontSize: button.fontSize
                paintedSize: Style.bar.iconSize
                color: root.tint
            }
        }

        Text {
            id: numberLabel
            visible: root.number !== ""
            text: root.number
            // never markup, whatever the engine prints (D114)
            textFormat: Text.PlainText
            x: Style.bar.iconSlot
            anchors.verticalCenter: parent.verticalCenter
            color: root.tint
            font.family: button.fontFamily
            font.pixelSize: button.fontSize
            font.bold: true
            renderType: Text.NativeRendering
        }
    }
}
